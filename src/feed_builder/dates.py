"""Date parsing and ISO 8601 interval formatting for sale windows."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple


def parse_tz(text: str) -> timezone:
    """Parse '+06:00', '-0530', 'UTC' or 'Z' into a fixed-offset timezone."""
    t = (text or "+00:00").strip()
    if t.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", t)
    if not m:
        raise ValueError("invalid timezone offset %r (use e.g. +06:00)" % text)
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return timezone(delta if m.group(1) == "+" else -delta)


def parse_dt(value: str, default_tz: timezone = timezone.utc, end_of_day: bool = False) -> Optional[datetime]:
    """Parse ISO 8601 dates/datetimes. Naive values are assumed to be in default_tz."""
    s = (value or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d.*", s):
        s = s.replace(" ", "T", 1)
    s = re.sub(r"(T.*[+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            dt = datetime.combine(datetime.fromisoformat(s).date(), time(23, 59) if end_of_day else time(0, 0))
        else:
            dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=default_tz)


def _fmt(dt: datetime, tz: timezone) -> str:
    local = dt.astimezone(tz)
    offset = local.utcoffset() or timedelta(0)
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%s%02d:%02d" % (local.strftime("%Y-%m-%dT%H:%M"), sign, total // 60, total % 60)


def format_interval(start: datetime, end: datetime, tz: timezone = timezone.utc) -> str:
    """Format a sale window like 2026-03-01T00:00+06:00/2026-03-31T23:59+06:00."""
    return "%s/%s" % (_fmt(start, tz), _fmt(end, tz))


def parse_interval(value: str, default_tz: timezone = timezone.utc) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Parse 'start/end' into datetimes; (None, None) when it cannot be parsed."""
    if not value or "/" not in value:
        return None, None
    left, right = value.split("/", 1)
    return parse_dt(left, default_tz), parse_dt(right, default_tz, end_of_day=True)
