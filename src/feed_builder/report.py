"""Validation report: JSON, CSV or plain text."""
from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict

from . import __version__
from .pipeline import BuildResult

CSV_COLUMNS = ["channel", "severity", "product_id", "code", "field", "message"]


def to_dict(result: BuildResult) -> Dict[str, Any]:
    channels = {}
    for name, ch in result.channels.items():
        channels[name] = {
            "products_in_feed": len(ch.records),
            "products_rejected": len(ch.invalid),
            "errors": len(ch.errors),
            "warnings": len(ch.warnings),
            "rejected": [{"product_id": pid, "reason": reason} for pid, reason in ch.invalid],
            "issues": [i.as_dict() for i in ch.issues],
        }
    return {
        "tool": "feed-builder",
        "version": __version__,
        "products_read": result.total_input,
        "filtered_out": [{"product_id": pid, "reason": reason} for pid, reason in result.filtered],
        "channels": channels,
    }


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(result: BuildResult) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for ch in result.channels.values():
        for issue in ch.issues:
            writer.writerow({k: _cell(v) for k, v in issue.as_dict().items()})
    return buf.getvalue()


def to_text(result: BuildResult) -> str:
    lines = ["Products read: %d, filtered out: %d" % (result.total_input, len(result.filtered))]
    for name, ch in result.channels.items():
        lines.append("")
        lines.append("[%s] in feed: %d, rejected: %d, errors: %d, warnings: %d" % (
            name, len(ch.records), len(ch.invalid), len(ch.errors), len(ch.warnings)))
        for i in ch.issues:
            lines.append("  %-7s %-20s %-18s %s" % (i.severity.upper(), i.product_id, i.code, i.message))
    return "\n".join(lines) + "\n"


def write_report(result: BuildResult, path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        text = json.dumps(to_dict(result), indent=2, ensure_ascii=False) + "\n"
    elif ext == ".csv":
        text = to_csv(result)
    else:
        text = to_text(result)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
