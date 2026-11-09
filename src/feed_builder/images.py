"""Optional image URL reachability checks (HEAD, falling back to a ranged GET)."""
from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlsplit

import requests

from . import __version__


def is_private_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("localhost", "") or host.endswith(".localhost") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def check_one(url: str, session: Any, timeout: float) -> Optional[str]:
    """Return None when the image is reachable, otherwise a short reason."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "unsupported scheme"
    if is_private_host(parts.hostname or ""):
        return "refusing to request a private or local address"
    headers = {"User-Agent": "feed-builder/%s" % __version__}
    try:
        resp = session.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code in (403, 405, 501):
            resp = session.get(url, headers=dict(headers, Range="bytes=0-0"), timeout=timeout,
                               allow_redirects=True, stream=True)
            close = getattr(resp, "close", None)
            if close:
                close()
    except requests.RequestException as exc:
        return "request failed: %s" % type(exc).__name__
    if resp.status_code >= 400:
        return "HTTP %d" % resp.status_code
    ctype = str(resp.headers.get("Content-Type", "")).lower()
    if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
        return "not an image (Content-Type %s)" % ctype.split(";")[0]
    return None


def check_images(
    urls: Sequence[str], session: Any = None, timeout: float = 10.0, workers: int = 8
) -> Dict[str, Optional[str]]:
    session = session or requests.Session()
    urls = list(urls)
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as pool:
        return dict(zip(urls, pool.map(lambda u: check_one(u, session, timeout), urls)))
