"""Read-only API client: only GET requests are possible."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests

from . import __version__
from .models import SourceError

RETRY_STATUSES = {429, 500, 502, 503, 504}


def require_https(url: str) -> None:
    """Credentials must never travel over plain HTTP (except to a local development host)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost") or host.endswith(".test")
    if parts.scheme != "https" and not local:
        raise SourceError("refusing to send credentials over %s://; use an https:// store URL" % parts.scheme)


class ApiClient:
    """Thin wrapper over requests that exposes GET only, with retry and Retry-After support."""

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        auth: Optional[Tuple[str, str]] = None,
        session: Any = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.headers = {"User-Agent": "feed-builder/%s" % __version__, "Accept": "application/json"}
        self.headers.update(headers or {})
        self.auth = auth
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        require_https(url)
        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(
                    url, params=params, headers=self.headers, auth=self.auth, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = "%s: %s" % (type(exc).__name__, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                raise SourceError("request to %s failed: %s" % (url, last_error)) from exc
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = str(resp.headers.get("Retry-After", "")).strip()
                wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
                self._sleep(min(wait, 60))
                continue
            if resp.status_code in (401, 403):
                raise SourceError("authentication failed (HTTP %d) for %s; check your API credentials" % (resp.status_code, url))
            if resp.status_code >= 400:
                raise SourceError("HTTP %d from %s" % (resp.status_code, url))
            return resp
        raise SourceError("request to %s failed: %s" % (url, last_error))
