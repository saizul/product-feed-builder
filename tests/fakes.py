"""Fake HTTP session: no network access in tests."""
import json

import requests


class FakeResponse:
    def __init__(self, data=None, status=200, headers=None, text=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        return self._data

    def close(self):
        pass


class FakeSession:
    """Routes are url -> response | list of responses | callable(params) -> response."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def _dispatch(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params"), kw))
        route = self.routes.get(url)
        if route is None:
            return FakeResponse({"message": "not found"}, 404)
        if callable(route):
            route = route(kw.get("params") or {})
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, Exception):
            raise route
        return route

    def get(self, url, **kw):
        return self._dispatch("GET", url, **kw)

    def head(self, url, **kw):
        return self._dispatch("HEAD", url, **kw)

    # Anything below must never be called by a read-only tool.
    def post(self, url, **kw):  # pragma: no cover
        raise AssertionError("POST is not allowed")

    put = patch = delete = post


ConnectionError_ = requests.ConnectionError


def sleeper():
    sleeps = []
    return sleeps, sleeps.append
