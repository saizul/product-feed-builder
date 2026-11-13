"""Lint an existing feed file against the channel specification.

`build` validates products before they are written. This module checks a feed
that already exists on disk (one written by `build` and edited by hand, or one
produced by another tool) using the same channel specs, so problems can be
caught before Merchant Center or Commerce Manager fetches the file.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .channels import AGE_GROUPS, CHANNELS, GENDERS, ChannelSpec
from .dates import parse_interval
from .diff import FeedItems, Value
from .gtin import validate_gtin
from .models import FeedBuilderError, FeedIssue
from .report import CSV_COLUMNS, _cell

# Attributes that tell variants of one item_group_id apart.
VARIANT_ATTRIBUTES = ("color", "size", "material", "pattern", "gender", "age_group")
_PRICE_RE = re.compile(r"^(\d+(?:\.\d+)?) ([A-Z]{3})$")
_HTML_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>|&(?:[a-zA-Z]+|#\d+);")
_SEVERITY_ORDER = {"error": 0, "warning": 1}


@dataclass
class ValidationResult:
    channel: str
    items: int
    issues: List[FeedIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[FeedIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[FeedIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def items_with_errors(self) -> List[str]:
        seen: Dict[str, None] = {}
        for i in self.errors:
            if i.product_id:
                seen.setdefault(i.product_id, None)
        return list(seen)

    def code_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for i in self.issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _as_list(value: Optional[Value]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v]
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_price(value: str) -> Optional[Tuple[Decimal, str]]:
    m = _PRICE_RE.match(value)
    if not m:
        return None
    try:
        return Decimal(m.group(1)), m.group(2)
    except InvalidOperation:  # pragma: no cover - the regex only admits valid decimals
        return None


class _ItemLinter:
    def __init__(self, pid: str, fields: Dict[str, Value], spec: ChannelSpec, now: datetime, out: List[FeedIssue]):
        self.pid, self.fields, self.spec, self.now, self.out = pid, fields, spec, now, out

    def add(self, severity: str, code: str, fname: str, message: str) -> None:
        self.out.append(FeedIssue(self.pid, self.spec.name, severity, code, fname, message))

    def text(self, name: str) -> str:
        value = self.fields.get(name)
        if isinstance(value, list):
            return value[0] if value else ""
        return value or ""

    def run(self) -> None:
        self.required()
        self.lengths()
        self.prices()
        self.sale_dates()
        self.urls()
        self.enums()
        self.identifiers()
        self.content()
        if self.spec.name == "google":
            self.apparel()

    def required(self) -> None:
        for name in self.spec.required:
            if not self.text(name):
                self.add("error", "missing_required", name, "required field %r is empty" % name)

    def lengths(self) -> None:
        for name in self.fields:
            key = "custom_label" if name.startswith("custom_label") else name
            limit = self.spec.limits.get(key)
            if not limit:
                continue
            for value in _as_list(self.fields[name]) if name == "additional_image_link" else [self.text(name)]:
                if len(value) > limit:
                    self.add("error", "too_long", name, "%s is %d characters; limit is %d" % (name, len(value), limit))

    def prices(self) -> None:
        parsed: Dict[str, Tuple[Decimal, str]] = {}
        for name in ("price", "sale_price"):
            raw = self.text(name)
            if not raw:
                continue
            price = _parse_price(raw)
            if price is None:
                self.add(
                    "error",
                    "price_invalid",
                    name,
                    "%s %r must be a number followed by an ISO 4217 code, e.g. 1299.00 BDT" % (name, raw),
                )
                continue
            if name == "price" and price[0] <= 0:
                self.add("error", "price_not_positive", name, "price must be greater than zero")
            parsed[name] = price
        if "price" in parsed and "sale_price" in parsed:
            (amount, cur), (sale, sale_cur) = parsed["price"], parsed["sale_price"]
            if cur != sale_cur:
                self.add(
                    "error",
                    "currency_mismatch",
                    "sale_price",
                    "sale_price currency %s differs from price currency %s" % (sale_cur, cur),
                )
            elif sale >= amount:
                self.add(
                    "error",
                    "sale_price_not_lower",
                    "sale_price",
                    "sale price %s is not lower than price %s" % (sale, amount),
                )
        if self.text("sale_price_effective_date") and not self.text("sale_price"):
            self.add(
                "warning",
                "sale_dates_without_price",
                "sale_price_effective_date",
                "sale_price_effective_date is set but there is no sale_price",
            )

    def sale_dates(self) -> None:
        raw = self.text("sale_price_effective_date")
        if not raw:
            return
        start, end = parse_interval(raw)
        if start is None or end is None:
            self.add(
                "error",
                "sale_dates_invalid",
                "sale_price_effective_date",
                "%r is not an ISO 8601 interval like 2026-03-01T00:00+06:00/2026-03-31T23:59+06:00" % raw,
            )
        elif end <= start:
            self.add("error", "sale_dates_invalid", "sale_price_effective_date", "sale end is not after sale start")
        elif end < self.now:
            self.add("warning", "sale_expired", "sale_price_effective_date", "sale ended %s" % end.isoformat())

    def urls(self) -> None:
        targets = [("link", self.text("link")), ("image_link", self.text("image_link"))]
        targets += [("additional_image_link", u) for u in _as_list(self.fields.get("additional_image_link"))]
        for name, url in targets:
            if not url:
                continue
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc or " " in url:
                severity = "warning" if name == "additional_image_link" else "error"
                self.add(severity, "url_invalid", name, "%s is not a valid absolute http(s) URL: %r" % (name, url[:80]))
                continue
            if name != "link":
                base = parts.path.rsplit("/", 1)[-1].lower()
                if "placeholder" in base:
                    severity = "error" if name == "image_link" else "warning"
                    self.add(severity, "image_placeholder", name, "image looks like a placeholder: %s" % base)
                elif base.endswith(".svg"):
                    self.add("warning", "image_format", name, "SVG images are not supported by Merchant Center/Meta")
        extra = _as_list(self.fields.get("additional_image_link"))
        if len(extra) > self.spec.max_additional_images:
            self.add(
                "warning",
                "additional_images_truncated",
                "additional_image_link",
                "%d additional images; the channel uses at most %d" % (len(extra), self.spec.max_additional_images),
            )
        if self.text("image_link") and self.text("image_link") in extra:
            self.add(
                "warning",
                "image_duplicated",
                "additional_image_link",
                "image_link is repeated in additional_image_link",
            )

    def enums(self) -> None:
        availability = self.text("availability")
        allowed = sorted(set(self.spec.availability_labels.values()))
        if availability and availability not in allowed:
            hint = ""
            for canonical, label in self.spec.availability_labels.items():
                if availability.lower().replace(" ", "_") == canonical:
                    hint = " (use %r for %s)" % (label, self.spec.label)
            self.add(
                "error",
                "availability_invalid",
                "availability",
                "availability %r must be one of %s%s" % (availability, ", ".join(allowed), hint),
            )
        condition = self.text("condition")
        if condition and condition not in self.spec.conditions:
            self.add(
                "error",
                "condition_invalid",
                "condition",
                "condition %r must be one of %s" % (condition, ", ".join(self.spec.conditions)),
            )
        for name, values in (("gender", GENDERS), ("age_group", AGE_GROUPS)):
            value = self.text(name)
            if value and value not in values:
                self.add(
                    "warning", "enum_invalid", name, "%s %r is not one of %s" % (name, value, ", ".join(sorted(values)))
                )
        inventory = self.text("inventory")
        if inventory and not inventory.lstrip("-").isdigit():
            self.add("warning", "inventory_invalid", "inventory", "inventory %r is not a whole number" % inventory)
        identifier_exists = self.text("identifier_exists")
        if identifier_exists and identifier_exists not in ("yes", "no"):
            self.add("error", "enum_invalid", "identifier_exists", "identifier_exists must be yes or no")

    def identifiers(self) -> None:
        gtin = self.text("gtin")
        if gtin:
            ok, why = validate_gtin(gtin)
            if not ok:
                self.add("error", "gtin_invalid", "gtin", "%s: %s" % (gtin, why))
        if self.spec.name != "google":
            return
        has_ids = bool(gtin) or (bool(self.text("brand")) and bool(self.text("mpn")))
        if not has_ids and self.text("identifier_exists") != "no":
            self.add(
                "warning",
                "missing_identifier",
                "gtin",
                "no gtin and no brand + mpn; set identifier_exists to no if the product has no identifiers",
            )
        if gtin and self.text("identifier_exists") == "no":
            self.add(
                "warning", "identifier_conflict", "identifier_exists", "identifier_exists is no but a gtin is present"
            )

    def content(self) -> None:
        title = self.text("title")
        if len(title) >= 8 and title == title.upper() and any(c.isalpha() for c in title):
            self.add("warning", "title_all_caps", "title", "title is all capitals; stores are asked to avoid this")
        for name in ("title", "description"):
            if _HTML_RE.search(self.text(name)):
                self.add("warning", "html_in_text", name, "%s contains HTML tags or entities" % name)
        description = self.text("description")
        if title and description and title.strip().lower() == description.strip().lower():
            self.add("warning", "description_same_as_title", "description", "description only repeats the title")

    def apparel(self) -> None:
        cat = self.text("google_product_category") + " " + self.text("product_type")
        if not (cat.lower().startswith("apparel & accessories") or "1604" in cat.split()):
            return
        for name in ("gender", "age_group"):
            if not self.text(name):
                self.add("error", "missing_required", name, "%r is required for apparel products" % name)
        for name in ("color", "size"):
            if not self.text(name):
                self.add("warning", "missing_recommended", name, "%r is recommended for apparel products" % name)


def _variant_groups(feed: FeedItems, spec: ChannelSpec, out: List[FeedIssue]) -> None:
    groups: Dict[str, List[str]] = {}
    for pid, fields in feed.items.items():
        gid = fields.get("item_group_id")
        if isinstance(gid, str) and gid:
            groups.setdefault(gid, []).append(pid)
    for gid, members in groups.items():
        if gid in feed.items and gid not in members:
            out.append(
                FeedIssue(
                    gid,
                    spec.name,
                    "warning",
                    "variant_group_id_conflict",
                    "item_group_id",
                    "item_group_id %r is also the id of a product outside the group" % gid,
                )
            )
        if len(members) < 2:
            continue
        signatures: Dict[Tuple[str, ...], str] = {}
        for pid in members:
            fields = feed.items[pid]
            sig = tuple(str(fields.get(a, "")).strip().lower() for a in VARIANT_ATTRIBUTES)
            if not any(sig):
                out.append(
                    FeedIssue(
                        pid,
                        spec.name,
                        "warning",
                        "variant_attributes_missing",
                        "item_group_id",
                        "variant of group %r has none of %s" % (gid, ", ".join(VARIANT_ATTRIBUTES)),
                    )
                )
            elif sig in signatures:
                out.append(
                    FeedIssue(
                        pid,
                        spec.name,
                        "warning",
                        "variant_duplicate",
                        "item_group_id",
                        "variant has the same %s as %s in group %r"
                        % ("/".join(VARIANT_ATTRIBUTES), signatures[sig], gid),
                    )
                )
            else:
                signatures[sig] = pid
        brands = sorted({str(feed.items[pid].get("brand", "")) for pid in members} - {""})
        if len(brands) > 1:
            out.append(
                FeedIssue(
                    members[0],
                    spec.name,
                    "warning",
                    "variant_brand_mismatch",
                    "brand",
                    "variants of group %r use different brands: %s" % (gid, ", ".join(brands)),
                )
            )


def validate_feed(feed: FeedItems, channel: Optional[str] = None, now: Optional[datetime] = None) -> ValidationResult:
    """Check a parsed feed against a channel spec; the channel defaults to the detected feed kind."""
    name = channel or feed.kind
    if name not in CHANNELS:
        raise FeedBuilderError("unknown channel %r (choose from %s)" % (name, ", ".join(CHANNELS)))
    if feed.kind == "google" and name != "google":
        raise FeedBuilderError("an XML feed can only be validated as a google feed")
    spec = CHANNELS[name]
    now = now or datetime.now(timezone.utc)
    issues: List[FeedIssue] = []
    if not feed.items:
        issues.append(FeedIssue("", name, "error", "feed_empty", "", "the feed contains no items"))
    for label in feed.missing_ids:
        issues.append(FeedIssue(label, name, "error", "missing_required", "id", "%s has no id" % label))
    for pid in feed.duplicates:
        issues.append(FeedIssue(pid, name, "error", "duplicate_id", "id", "id %r appears more than once" % pid))
    for pid, fields in feed.items.items():
        _ItemLinter(pid, fields, spec, now, issues).run()
    _variant_groups(feed, spec, issues)
    return ValidationResult(channel=name, items=len(feed.items), issues=issues)


# --------------------------------------------------------------------------- reports
def to_dict(result: ValidationResult) -> Dict[str, Any]:
    return {
        "channel": result.channel,
        "summary": {
            "items": result.items,
            "items_with_errors": len(result.items_with_errors()),
            "errors": len(result.errors),
            "warnings": len(result.warnings),
            "by_code": result.code_counts(),
        },
        "issues": [i.as_dict() for i in result.issues],
    }


def to_json(result: ValidationResult) -> str:
    return json.dumps(to_dict(result), indent=2, ensure_ascii=False) + "\n"


def to_csv(result: ValidationResult) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for issue in result.issues:
        writer.writerow({k: _cell(v) for k, v in issue.as_dict().items()})
    return buf.getvalue()


def to_text(result: ValidationResult, limit: int = 0) -> str:
    """Human readable report, errors first. `limit` caps the issue list (0 means no cap)."""
    spec = CHANNELS[result.channel]
    lines = [
        "%s feed: %d item(s), %d with errors, %d error(s), %d warning(s)"
        % (spec.label, result.items, len(result.items_with_errors()), len(result.errors), len(result.warnings))
    ]
    ordered = sorted(result.issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 2))
    shown = ordered[:limit] if limit else ordered
    if shown:
        lines.append("")
    for i in shown:
        lines.append("  %-7s %-20s %-26s %s" % (i.severity.upper(), i.product_id or "-", i.code, i.message))
    if limit and len(ordered) > limit:
        lines.append("  ... and %d more" % (len(ordered) - limit))
    if result.issues:
        lines += ["", "By code: " + ", ".join("%s (%d)" % kv for kv in result.code_counts().items())]
    else:
        lines.append("No problems found.")
    return "\n".join(lines) + "\n"
