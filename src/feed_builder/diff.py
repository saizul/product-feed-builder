"""Compare two generated feeds: which items were added, removed or changed."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .models import FeedBuilderError

Value = Union[str, List[str]]
Items = Dict[str, Dict[str, Value]]


@dataclass
class FeedItems:
    kind: str  # "google" or "meta"
    items: Items = field(default_factory=dict)
    duplicates: List[str] = field(default_factory=list)
    missing_ids: List[str] = field(default_factory=list)  # row labels of CSV rows without an id


@dataclass
class FeedDiff:
    kind: str
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: Dict[str, Dict[str, Tuple[Optional[Value], Optional[Value]]]] = field(default_factory=dict)
    unchanged: int = 0
    duplicates_old: List[str] = field(default_factory=list)
    duplicates_new: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def field_counts(self) -> Dict[str, int]:
        """How many items changed per field, most common first."""
        counts: Dict[str, int] = {}
        for fields in self.changed.values():
            for name in fields:
                counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element) -> str:
    return (el.text or "").strip()


def _parse_google(data: bytes) -> FeedItems:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedBuilderError("not a valid XML feed: %s" % exc) from exc
    out = FeedItems("google")
    for item in root.iter("item"):
        fields: Dict[str, Value] = {}
        for child in item:
            name = _local(child.tag)
            if name == "shipping":
                parts = {_local(c.tag): _text(c) for c in child}
                value = ":".join(parts.get(k, "") for k in ("country", "service", "price"))
            else:
                value = _text(child)
            if value == "" or value == "::":
                continue
            if name in ("additional_image_link", "shipping"):
                fields.setdefault(name, [])
                fields[name].append(value)  # type: ignore[union-attr]
            else:
                fields[name] = value
        pid = fields.get("id")
        if not isinstance(pid, str) or not pid:
            raise FeedBuilderError("an <item> without g:id was found; is this a feed built by feed-builder?")
        if pid in out.items:
            out.duplicates.append(pid)
            continue
        out.items[pid] = fields
    return out


def _parse_meta(data: bytes) -> FeedItems:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or "id" not in reader.fieldnames:
        raise FeedBuilderError("the CSV feed has no `id` column")
    out = FeedItems("meta")
    for row in reader:
        fields: Dict[str, Value] = {k: v.strip() for k, v in row.items() if k and isinstance(v, str) and v.strip()}
        pid = fields.get("id")
        if not isinstance(pid, str):
            if fields:
                out.missing_ids.append("row %d" % reader.line_num)
            continue
        if pid in out.items:
            out.duplicates.append(pid)
            continue
        out.items[pid] = fields
    return out


def parse_feed(data: bytes) -> FeedItems:
    """Parse a Google RSS/XML or Meta CSV feed; the format is detected from the content."""
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if head == b"<":
        return _parse_google(data)
    return _parse_meta(data)


def load_feed(path: str) -> FeedItems:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise FeedBuilderError("cannot read %s: %s" % (path, exc)) from exc
    try:
        return parse_feed(data)
    except FeedBuilderError as exc:
        raise FeedBuilderError("%s: %s" % (path, exc)) from exc


def diff_feeds(old: FeedItems, new: FeedItems, ignore: Iterable[str] = ()) -> FeedDiff:
    """Compare two parsed feeds item by item (matched on id), optionally ignoring fields."""
    if old.kind != new.kind:
        raise FeedBuilderError("cannot compare a %s feed with a %s feed" % (old.kind, new.kind))
    skip = set(ignore)
    result = FeedDiff(kind=new.kind, duplicates_old=old.duplicates, duplicates_new=new.duplicates)
    result.added = sorted(set(new.items) - set(old.items))
    result.removed = sorted(set(old.items) - set(new.items))
    for pid in sorted(set(old.items) & set(new.items)):
        a, b = old.items[pid], new.items[pid]
        deltas = {
            name: (a.get(name), b.get(name)) for name in sorted((set(a) | set(b)) - skip) if a.get(name) != b.get(name)
        }
        if deltas:
            result.changed[pid] = deltas
        else:
            result.unchanged += 1
    return result


def _show(value: Optional[Value]) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, list):
        return ", ".join(value)
    return value if len(value) <= 80 else value[:77] + "..."


def to_dict(diff: FeedDiff) -> Dict[str, Any]:
    return {
        "feed": diff.kind,
        "summary": {
            "added": len(diff.added),
            "removed": len(diff.removed),
            "changed": len(diff.changed),
            "unchanged": diff.unchanged,
            "changed_by_field": diff.field_counts(),
        },
        "added": diff.added,
        "removed": diff.removed,
        "changed": {
            pid: {name: {"old": old, "new": new} for name, (old, new) in fields.items()}
            for pid, fields in diff.changed.items()
        },
        "duplicate_ids": {"old": diff.duplicates_old, "new": diff.duplicates_new},
    }


def to_json(diff: FeedDiff) -> str:
    return json.dumps(to_dict(diff), indent=2, ensure_ascii=False) + "\n"


def to_text(diff: FeedDiff, limit: int = 50) -> str:
    """Human readable report. `limit` caps each list (0 means no cap)."""
    lines = [
        "%s feed: %d added, %d removed, %d changed, %d unchanged"
        % (diff.kind, len(diff.added), len(diff.removed), len(diff.changed), diff.unchanged)
    ]

    def capped(seq: List[Any]) -> List[Any]:
        return seq[:limit] if limit else seq

    def more(total: int) -> List[str]:
        return ["  ... and %d more" % (total - limit)] if limit and total > limit else []

    if diff.added:
        lines += (
            ["", "Added (%d):" % len(diff.added)]
            + ["  + %s" % pid for pid in capped(diff.added)]
            + more(len(diff.added))
        )
    if diff.removed:
        lines += (
            ["", "Removed (%d):" % len(diff.removed)]
            + ["  - %s" % pid for pid in capped(diff.removed)]
            + more(len(diff.removed))
        )
    if diff.changed:
        lines += ["", "Changed (%d):" % len(diff.changed)]
        for pid in capped(list(diff.changed)):
            lines.append("  ~ %s" % pid)
            for name, (old, new) in diff.changed[pid].items():
                lines.append("      %s: %s -> %s" % (name, _show(old), _show(new)))
        lines += more(len(diff.changed))
        lines += ["", "Changed fields: " + ", ".join("%s (%d)" % kv for kv in diff.field_counts().items())]
    for label, dups in (("old", diff.duplicates_old), ("new", diff.duplicates_new)):
        if dups:
            lines += [
                "",
                "Warning: %d duplicate id(s) in the %s feed (first occurrence used): %s"
                % (len(dups), label, ", ".join(sorted(set(dups))[:10])),
            ]
    return "\n".join(lines) + "\n"
