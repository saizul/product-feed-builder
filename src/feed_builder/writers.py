"""Feed writers: Google RSS 2.0 (g: namespace) and Meta catalog CSV."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence

from .channels import CHANNELS
from .config import Config
from .pipeline import Record

G_NS = "http://base.google.com/ns/1.0"
ET.register_namespace("g", G_NS)


def _g(tag: str) -> str:
    return "{%s}%s" % (G_NS, tag)


def google_xml(records: Sequence[Record], cfg: Config) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = cfg.feed_title
    ET.SubElement(channel, "link").text = cfg.feed_link or cfg.store_url or "https://example.com"
    ET.SubElement(channel, "description").text = cfg.feed_description
    order = CHANNELS["google"].field_order
    for rec in records:
        item = ET.SubElement(channel, "item")
        names = [n for n in order if n in rec.fields] + sorted(n for n in rec.fields if n not in order)
        for name in names:
            value = rec.fields[name]
            if name == "additional_image_link":
                for url in value:
                    ET.SubElement(item, _g(name)).text = url
            elif name == "shipping":
                for row in value:
                    ship = ET.SubElement(item, _g("shipping"))
                    ET.SubElement(ship, _g("country")).text = row["country"]
                    if row.get("service"):
                        ET.SubElement(ship, _g("service")).text = row["service"]
                    ET.SubElement(ship, _g("price")).text = row["price"]
            else:
                ET.SubElement(item, _g(name)).text = str(value)
    ET.indent(rss, space="  ")
    body = ET.tostring(rss, encoding="unicode")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body.encode("utf-8") + b"\n"


def meta_csv_columns(records: Sequence[Record]) -> List[str]:
    order = CHANNELS["meta"].field_order
    present = {n for r in records for n in r.fields}
    required = CHANNELS["meta"].required
    cols = [n for n in order if n in present or n in required]
    cols += sorted(present - set(order))
    return cols


def meta_csv(records: Sequence[Record], cfg: Optional[Config] = None) -> bytes:
    cols = meta_csv_columns(records)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cols)
    for rec in records:
        row = []
        for c in cols:
            v = rec.fields.get(c, "")
            row.append(",".join(v) if isinstance(v, list) else v)
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def render_channel(channel: str, records: Sequence[Record], cfg: Config) -> bytes:
    if channel == "google":
        return google_xml(records, cfg)
    if channel == "meta":
        return meta_csv(records, cfg)
    raise ValueError("unknown channel %r" % channel)


def write_if_changed(path: str, data: bytes) -> bool:
    """Atomically write data; returns False (and leaves the file untouched) when content is identical."""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() == data:
                return False
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return True


def record_hash(fields: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

