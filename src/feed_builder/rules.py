"""Condition language used by filters and custom-label rules."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

from .models import ConfigError
from .templates import lookup

OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "intersects", "regex", "empty", "not_empty"}


def validate_condition(cond: Any, where: str = "condition") -> None:
    if not isinstance(cond, dict) or not cond:
        raise ConfigError("%s must be a mapping" % where)
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        if not isinstance(cond[key], list) or not cond[key]:
            raise ConfigError("%s: '%s' needs a non-empty list" % (where, key))
        for sub in cond[key]:
            validate_condition(sub, where)
        return
    if "not" in cond:
        validate_condition(cond["not"], where)
        return
    if "field" not in cond or "op" not in cond:
        raise ConfigError("%s needs 'field' and 'op' (got %s)" % (where, sorted(cond)))
    if cond["op"] not in OPS:
        raise ConfigError("%s: unknown op %r (use one of %s)" % (where, cond["op"], ", ".join(sorted(OPS))))
    if cond["op"] not in ("empty", "not_empty") and "value" not in cond:
        raise ConfigError("%s: op %r needs a 'value'" % (where, cond["op"]))
    if cond["op"] == "regex":
        try:
            re.compile(str(cond["value"]))
        except re.error as exc:
            raise ConfigError("%s: invalid regex: %s" % (where, exc)) from exc


def _num(v: Any):
    if isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _lower(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


def evaluate(cond: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    if "all" in cond:
        return all(evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], ctx)
    actual = lookup(str(cond["field"]), ctx)
    op = cond["op"]
    expected = cond.get("value")
    if op == "empty":
        return actual in (None, "", [], {})
    if op == "not_empty":
        return actual not in (None, "", [], {})
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _num(actual), _num(expected)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "eq":
        if isinstance(actual, bool) or isinstance(expected, bool):
            return actual is expected or actual == expected
        a, b = _num(actual), _num(expected)
        if a is not None and b is not None and not isinstance(actual, str):
            return a == b
        return _lower(actual) == _lower(expected)
    if op == "ne":
        return not evaluate({**cond, "op": "eq"}, ctx)
    if op in ("in", "not_in"):
        pool = [_lower(x) for x in (expected if isinstance(expected, list) else [expected])]
        result = _lower(actual) in pool
        return result if op == "in" else not result
    if op == "contains":
        if isinstance(actual, (list, tuple)):
            return _lower(expected) in [_lower(x) for x in actual]
        return _lower(str(expected)) in str(actual or "").lower()
    if op == "intersects":
        mine = {_lower(x) for x in (actual if isinstance(actual, (list, tuple)) else [actual])}
        theirs = {_lower(x) for x in (expected if isinstance(expected, list) else [expected])}
        return bool(mine & theirs)
    if op == "regex":
        return re.search(str(expected), str(actual or ""), re.I) is not None
    return False
