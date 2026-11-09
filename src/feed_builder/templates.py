"""Tiny template language for field mapping: '{title|truncate:70}', 'Buy {brand} {title}'."""
from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Any, Callable, Dict, List

from .models import ConfigError

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", " ", text)
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary where possible, ending with an ellipsis, never exceeding limit."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    cut = text[: limit - 3]
    space = cut.rfind(" ")
    if space >= (limit - 3) * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "..."


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _f_join(value: Any, sep: str = ", ") -> str:
    if isinstance(value, (list, tuple)):
        return sep.join(_text(v) for v in value)
    return _text(value)


FILTERS: Dict[str, Callable[..., Any]] = {
    "upper": lambda v: _text(v).upper(),
    "lower": lambda v: _text(v).lower(),
    "title": lambda v: _text(v).title(),
    "strip": lambda v: _text(v).strip(),
    "strip_html": lambda v: strip_html(_text(v)),
    "truncate": lambda v, n="150": truncate(_text(v), int(n)),
    "default": lambda v, d="": v if v not in (None, "", []) else d,
    "prefix": lambda v, p="": (p + _text(v)) if _text(v) else "",
    "suffix": lambda v, s="": (_text(v) + s) if _text(v) else "",
    "join": _f_join,
    "replace": lambda v, old="", new="": _text(v).replace(old, new),
}


def lookup(path: str, ctx: Dict[str, Any]) -> Any:
    """Resolve 'attributes.color' style paths in a nested dict."""
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _eval(expr: str, ctx: Dict[str, Any]) -> Any:
    parts = expr.split("|")
    value = lookup(parts[0].strip(), ctx)
    for flt in parts[1:]:
        bits = flt.split(":")
        name, args = bits[0].strip(), bits[1:]
        func = FILTERS.get(name)
        if func is None:
            raise ConfigError("unknown template filter %r in {%s}" % (name, expr))
        try:
            value = func(value, *args)
        except (TypeError, ValueError) as exc:
            raise ConfigError("bad arguments for filter %r in {%s}: %s" % (name, expr, exc)) from exc
    return value


def placeholders(template: str) -> List[str]:
    return _PLACEHOLDER.findall(template)


def validate_template(template: str) -> None:
    """Raise ConfigError if the template references an unknown filter."""
    for expr in placeholders(template):
        for flt in expr.split("|")[1:]:
            name = flt.split(":")[0].strip()
            if name not in FILTERS:
                raise ConfigError("unknown template filter %r in {%s}" % (name, expr))


def render(template: Any, ctx: Dict[str, Any]) -> Any:
    """Render a template. A lone '{field}' returns the raw value (list, Decimal, ...)."""
    if not isinstance(template, str):
        return template
    m = _PLACEHOLDER.fullmatch(template.strip())
    if m:
        return _eval(m.group(1), ctx)
    return _PLACEHOLDER.sub(lambda mm: _text(_eval(mm.group(1), ctx)), template)
