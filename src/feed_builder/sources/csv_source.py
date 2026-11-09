"""CSV input: read a spreadsheet export with flexible column names."""
from __future__ import annotations

import csv
import re
from decimal import Decimal
from typing import Dict, Iterator, List, Optional

from ..dates import parse_dt, parse_interval, parse_tz
from ..models import Product, SourceError
from ..money import parse_money, split_money
from . import clean_list

ALIASES: Dict[str, List[str]] = {
    "id": ["id", "sku", "product_id", "item_id"],
    "title": ["title", "name", "product_name"],
    "description": ["description", "body", "body_html", "long_description"],
    "link": ["link", "url", "product_url", "product_link"],
    "image_link": ["image_link", "image", "image_url", "main_image"],
    "additional_images": ["additional_image_link", "additional_images", "images", "gallery"],
    "price": ["price", "regular_price"],
    "sale_price": ["sale_price", "special_price"],
    "sale_dates": ["sale_price_effective_date", "sale_dates"],
    "sale_start": ["sale_start", "sale_from", "sale_price_start"],
    "sale_end": ["sale_end", "sale_to", "sale_price_end"],
    "availability": ["availability", "stock_status", "in_stock"],
    "stock_quantity": ["quantity", "stock_quantity", "qty", "inventory"],
    "brand": ["brand", "vendor", "manufacturer"],
    "gtin": ["gtin", "ean", "upc", "barcode"],
    "mpn": ["mpn", "manufacturer_part_number"],
    "condition": ["condition"],
    "item_group_id": ["item_group_id", "parent_sku", "group_id"],
    "google_product_category": ["google_product_category", "google_category"],
    "product_type": ["product_type"],
    "categories": ["categories", "category"],
    "tags": ["tags"],
    "weight": ["weight", "shipping_weight"],
}
ATTRIBUTE_COLUMNS = ["color", "size", "material", "pattern", "gender", "age_group"]
_SPLIT = re.compile(r"\s*[|;,]\s*")


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def build_column_map(headers: List[str], overrides: Dict[str, str]) -> Dict[str, str]:
    """Map canonical field -> actual CSV header."""
    by_key = {_key(h): h for h in headers}
    mapping: Dict[str, str] = {}
    for canonical, header in overrides.items():
        if header not in headers and _key(header) not in by_key:
            raise SourceError("csv.columns: header %r (for %s) not found in the file" % (header, canonical))
        mapping[canonical] = header if header in headers else by_key[_key(header)]
    for canonical, names in ALIASES.items():
        if canonical in mapping:
            continue
        for name in names:
            if name in by_key:
                mapping[canonical] = by_key[name]
                break
    for attr in ATTRIBUTE_COLUMNS:
        if attr not in mapping and attr in by_key:
            mapping[attr] = by_key[attr]
    return mapping


class CsvSource:
    def __init__(self, path: str, column_overrides: Optional[Dict[str, str]] = None, delimiter: str = ",",
                 tz: str = "+00:00"):
        self.path = path
        self.overrides = column_overrides or {}
        self.delimiter = "\t" if path.lower().endswith(".tsv") and delimiter == "," else delimiter
        self.tz = parse_tz(tz)

    def fetch(self) -> Iterator[Product]:
        try:
            fh = open(self.path, encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise SourceError("cannot open CSV %s: %s" % (self.path, exc)) from exc
        with fh:
            reader = csv.DictReader(fh, delimiter=self.delimiter)
            if not reader.fieldnames:
                raise SourceError("CSV %s has no header row" % self.path)
            cols = build_column_map(list(reader.fieldnames), self.overrides)
            if "title" not in cols and "id" not in cols:
                raise SourceError("CSV needs at least an id/sku or title column (found: %s)" % ", ".join(reader.fieldnames))
            mapped = set(cols.values())
            for n, row in enumerate(reader, start=2):
                if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                    continue
                yield self._product(n, row, cols, mapped)

    def _get(self, row: Dict[str, str], cols: Dict[str, str], name: str) -> str:
        header = cols.get(name)
        return (row.get(header) or "").strip() if header else ""

    def _product(self, n: int, row: Dict[str, str], cols: Dict[str, str], mapped: set) -> Product:
        g = lambda name: self._get(row, cols, name)  # noqa: E731
        p = Product(
            id=g("id"), title=g("title"), description=g("description"), link=g("link"), image_link=g("image_link"),
            additional_images=clean_list(_split_urls(g("additional_images"))),
            availability=g("availability"), brand=g("brand"), gtin=g("gtin"), mpn=g("mpn"), sku=g("id"),
            condition=g("condition").lower(), item_group_id=g("item_group_id"),
            google_product_category=g("google_product_category"), product_type=g("product_type"),
            categories=clean_list(_SPLIT.split(g("categories"))) if g("categories") else [],
            tags=clean_list(_SPLIT.split(g("tags"))) if g("tags") else [],
            weight=g("weight"), source="csv",
        )
        p.category_slugs = [re.sub(r"[^a-z0-9]+", "-", c.lower()).strip("-") for c in p.categories]
        if not p.product_type and p.categories:
            p.product_type = p.categories[0]
        p.extra["_row_label"] = "row %d" % n
        p.extra["_row"] = n
        for attr in ATTRIBUTE_COLUMNS:
            if g(attr):
                p.attributes[attr] = g(attr)
        qty = g("stock_quantity")
        if qty:
            try:
                p.stock_quantity = int(Decimal(qty))
            except ArithmeticError:
                p.extra["_qty_raw"] = qty
        self._prices(p, g)
        for header in row:
            if header and header not in mapped and (row.get(header) or "").strip():
                p.extra[_key(header)] = row[header].strip()
        return p

    def _prices(self, p: Product, g) -> None:
        for field_name, attr in (("price", "price"), ("sale_price", "sale_price")):
            raw = g(field_name)
            if not raw:
                continue
            amount, currency = split_money(raw)
            amount = parse_money(raw) if amount is None else amount
            if amount is None:
                p.extra["_%s_raw" % attr] = raw
                continue
            setattr(p, attr, amount)
            if currency and not p.currency:
                p.currency = currency if len(currency) == 3 else ""
        if g("sale_dates"):
            p.sale_start, p.sale_end = parse_interval(g("sale_dates"), self.tz)
        else:
            p.sale_start = parse_dt(g("sale_start"), self.tz)
            p.sale_end = parse_dt(g("sale_end"), self.tz, end_of_day=True)


def _split_urls(text: str) -> List[str]:
    return re.split(r"\s*[|,]\s*|\s+", text) if text else []
