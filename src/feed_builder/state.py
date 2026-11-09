"""Incremental output: remember what was published last time and emit only the delta."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .models import FeedBuilderError
from .pipeline import Record
from .writers import record_hash

STATE_VERSION = 1


@dataclass
class Delta:
    new: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)

    @property
    def upserts(self) -> List[str]:
        return self.new + self.changed


def load_state(path: str) -> Dict[str, Dict[str, str]]:
    """Return {channel: {product_id: hash}}; empty when the file does not exist."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise FeedBuilderError("cannot read state file %s: %s" % (path, exc)) from exc
    if data.get("version") != STATE_VERSION:
        raise FeedBuilderError("state file %s has an unsupported version" % path)
    return {ch: dict(items) for ch, items in data.get("channels", {}).items()}


def save_state(path: str, state: Dict[str, Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": STATE_VERSION, "channels": state}, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def hashes(records: Sequence[Record]) -> Dict[str, str]:
    return {r.product_id: record_hash(r.fields) for r in records}


def compute_delta(previous: Dict[str, str], records: Sequence[Record]) -> Delta:
    current = hashes(records)
    delta = Delta()
    for pid, digest in current.items():
        if pid not in previous:
            delta.new.append(pid)
        elif previous[pid] != digest:
            delta.changed.append(pid)
        else:
            delta.unchanged.append(pid)
    delta.removed = sorted(set(previous) - set(current))
    return delta
