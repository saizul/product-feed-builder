"""Turn canonical products into validated channel records."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

from .channels import AGE_GROUPS, CHANNELS, CUSTOM_LABELS, GENDERS, ChannelSpec
from .config import Config
from .dates import format_interval, parse_tz
from .filters import apply_filters
from .gtin import clean_gtin, validate_gtin
from .models import AVAILABILITY_VALUES, ConfigError, FeedIssue, Product
from .money import format_money, parse_money, split_money
from .rules import evaluate
from .templates import render, strip_html, truncate

_XML_BAD = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")
_WS = re.compile(r"\s+")
AVAILABILITY_SYNONYMS = {
    "instock": "in_stock", "in_stock": "in_stock", "available": "in_stock", "yes": "in_stock", "true": "in_stock",
    "1": "in_stock", "y": "in_stock",
    "outofstock": "out_of_stock", "out_of_stock": "out_of_stock", "sold_out": "out_of_stock", "soldout": "out_of_stock",
    "unavailable": "out_of_stock", "no": "out_of_stock", "false": "out_of_stock", "0": "out_of_stock",
    "n": "out_of_stock", "discontinued": "out_of_stock",
    "preorder": "preorder", "pre_order": "preorder",
    "backorder": "backorder", "back_order": "backorder", "onbackorder": "backorder", "available_for_order": "backorder",
}


@dataclass
class Record:
    product_id: str
    fields: Dict[str, Any]


@dataclass
class ChannelResult:
    channel: str
    records: List[Record] = field(default_factory=list)
    issues: List[FeedIssue] = field(default_factory=list)
    invalid: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def errors(self) -> List[FeedIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[FeedIssue]:
        return [i for i in self.issues if i.severity == "warning"]


@dataclass
class BuildResult:
    channels: Dict[str, ChannelResult]
    filtered: List[Tuple[str, str]]
    total_input: int


ImageChecker = Callable[[Sequence[str]], Dict[str, Optional[str]]]


def normalize_availability(value: Any, custom: Optional[Dict[str, str]] = None) -> str:
    """Map store-specific stock values to in_stock/out_of_stock/preorder/backorder ('' if unknown)."""
    key = re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())
    if not key:
        return ""
    if custom and key in custom:
        return custom[key]
    if custom and str(value).strip().lower() in custom:
        return custom[str(value).strip().lower()]
    return AVAILABILITY_SYNONYMS.get(key, key if key in AVAILABILITY_VALUES else "")


def xml_safe(text: str) -> str:
    return _XML_BAD.sub("", text)


def clean_text(value: Any, html_strip: bool = False) -> str:
    text = xml_safe("" if value is None else str(value))
    if html_strip:
        text = strip_html(text)
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------- preparation
def prepare(p: Product, cfg: Config) -> None:
    """Apply currency, defaults and availability normalisation in place."""
    p.currency = (p.currency or cfg.currency).upper()
    d = cfg.defaults
    p.condition = (p.condition or d.get("condition", "new")).lower()
    for attr in ("brand", "google_product_category", "mpn"):
        if not getattr(p, attr) and d.get(attr):
            setattr(p, attr, d[attr])
    for attr in ("gender", "age_group", "color", "size", "material", "pattern"):
        if not p.attributes.get(attr) and d.get(attr):
            p.attributes[attr] = d[attr]
    raw = p.availability
    p.availability = normalize_availability(raw, cfg.availability_map)
    if raw and not p.availability:
        p.extra["_availability_raw"] = raw


def resolve_sale(
    p: Product, cfg: Config, now: datetime, tz: timezone
) -> Tuple[Optional[Decimal], str, List[Tuple[str, str]]]:
    """Return (sale_price, effective_date_interval, [(code, message)]) after sanity checks."""
    notes: List[Tuple[str, str]] = []
    if p.sale_price is None or p.price is None:
        return None, "", notes
    if p.sale_price >= p.price:
        notes.append(("sale_price_not_lower", "sale price %s is not lower than price %s; sale dropped" % (p.sale_price, p.price)))
        return None, "", notes
    start, end = p.sale_start, p.sale_end
    if start and end and end <= start:
        notes.append(("sale_dates_invalid", "sale end is not after sale start; sale dropped"))
        return None, "", notes
    if end and end < now and cfg.drop_expired_sales:
        notes.append(("sale_expired", "sale ended %s; sale price dropped" % end.isoformat()))
        return None, "", notes
    interval = ""
    if end and not start:
        interval = format_interval(now, end, tz)
    elif start and end:
        interval = format_interval(start, end, tz)
    elif start and not end:
        notes.append(("sale_dates_incomplete", "sale has a start date but no end date; dates omitted"))
    return p.sale_price, interval, notes


def product_context(p: Product, cfg: Config, now: datetime, tz: timezone) -> Dict[str, Any]:
    sale_price, interval, _notes = resolve_sale(p, cfg, now, tz)
    active = sale_price is not None and (not p.sale_start or p.sale_start <= now)
    ctx: Dict[str, Any] = {
        "id": p.id, "title": p.title, "description": p.description, "link": p.link, "image_link": p.image_link,
        "additional_images": list(p.additional_images), "price": p.price, "sale_price": sale_price,
        "sale_dates": interval if sale_price is not None else "", "on_sale": bool(active),
        "effective_price": sale_price if active else p.price, "currency": p.currency,
        "availability": p.availability, "stock_quantity": p.stock_quantity, "condition": p.condition,
        "brand": p.brand, "gtin": p.gtin, "mpn": p.mpn, "sku": p.sku, "item_group_id": p.item_group_id,
        "google_product_category": p.google_product_category, "product_type": p.product_type,
        "categories": list(p.categories), "category": p.categories[0] if p.categories else "",
        "tags": list(p.tags), "attributes": dict(p.attributes), "weight": p.weight, "extra": dict(p.extra),
        "source": p.source,
    }
    for name in CUSTOM_LABELS:
        value = ""
        for rule in cfg.label_rules.get(name, []):
            if evaluate(rule.when, ctx):
                value = rule.value
                break
        ctx[name] = value or cfg.label_defaults.get(name, "")
    return ctx


# --------------------------------------------------------------------------- field helpers
def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (list, tuple)):
        return ", ".join(_to_str(v) for v in value)
    return str(value)


def absolutize(url: str, base: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url) and base:
        url = urljoin(base.rstrip("/") + "/", url)
    return url.replace(" ", "%20")


def valid_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc) and len(url) <= 2000


class _RecordBuilder:
    def __init__(self, p: Product, spec: ChannelSpec, cfg: Config, ctx: Dict[str, Any], now: datetime, tz: timezone):
        self.p, self.spec, self.cfg, self.ctx, self.now, self.tz = p, spec, cfg, ctx, now, tz
        self.issues: List[FeedIssue] = []
        self.flagged: set = set()
        self.out: Dict[str, Any] = {}

    def add(self, severity: str, code: str, fname: str, message: str) -> None:
        self.issues.append(FeedIssue(self.p.label, self.spec.name, severity, code, fname, message))
        if severity == "error":
            self.flagged.add(fname)

    def limit(self, fname: str, text: str) -> str:
        key = "custom_label" if fname.startswith("custom_label") else fname
        max_len = self.spec.limits.get(key)
        if not max_len or len(text) <= max_len:
            return text
        if self.cfg.on_exceed == "error" or fname == "id" or fname.endswith("link"):
            self.add("error", "too_long", fname, "%s is %d characters; limit is %d" % (fname, len(text), max_len))
            return text
        self.add("warning", "truncated", fname, "%s truncated from %d to %d characters" % (fname, len(text), max_len))
        return truncate(text, max_len)

    # -- individual fields
    def money(self, fname: str, value: Any) -> str:
        if value in (None, ""):
            return ""
        amount, currency = split_money(value) if isinstance(value, str) else (parse_money(value), None)
        if amount is None:
            self.add("error", "price_invalid", fname, "cannot parse %s %r" % (fname, value))
            return ""
        currency = (currency if currency and len(currency) == 3 else None) or self.p.currency
        if not currency:
            self.add("error", "currency_missing", fname, "no currency configured for %s" % fname)
            return ""
        if fname == "price" and amount <= 0:
            self.add("error", "price_not_positive", fname, "price must be greater than zero")
            return ""
        return format_money(amount, currency)

    def url(self, fname: str, value: Any) -> str:
        url = absolutize(_to_str(value), self.cfg.store_url)
        if not url:
            return ""
        if not valid_url(url):
            self.add("error", "url_invalid", fname, "%s is not a valid absolute http(s) URL: %r" % (fname, url[:80]))
            return ""
        if fname == "image_link":
            name = urlsplit(url).path.rsplit("/", 1)[-1].lower()
            if "placeholder" in name:
                self.add("error", "image_placeholder", fname, "image looks like a placeholder: %s" % name)
            elif name.endswith(".svg"):
                self.add("warning", "image_format", fname, "SVG images are not supported by Merchant Center/Meta")
        return url

    def text(self, fname: str, value: Any) -> str:
        html_on = self.cfg.strip_html and fname in ("title", "description")
        text = clean_text(value, html_strip=html_on)
        if fname == "title" and len(text) >= 8 and text == text.upper() and any(c.isalpha() for c in text):
            self.add("warning", "title_all_caps", fname, "title is all capitals; stores are asked to avoid this")
        return self.limit(fname, text)

    def availability(self, value: Any) -> str:
        canonical = normalize_availability(value, self.cfg.availability_map)
        if not canonical:
            raw = self.p.extra.get("_availability_raw", value)
            self.add("error", "availability_invalid", "availability", "unrecognised availability %r" % (raw,))
            return ""
        return self.spec.availability_labels[canonical]

    def gtin(self, value: Any) -> str:
        code = clean_gtin(_to_str(value))
        if not code:
            return ""
        ok, why = validate_gtin(code)
        if ok:
            return code
        policy = self.cfg.invalid_gtin
        if policy == "keep":
            self.add("warning", "gtin_invalid", "gtin", "%s (%s); kept as configured" % (code, why))
            return code
        if policy == "error":
            self.add("error", "gtin_invalid", "gtin", "%s: %s" % (code, why))
            return ""
        self.add("warning", "gtin_invalid", "gtin", "%s: %s; GTIN dropped" % (code, why))
        return ""

    def enum(self, fname: str, value: Any, allowed: set) -> str:
        v = _to_str(value).strip().lower()
        if not v:
            return ""
        if v in ("m", "men", "man"):
            v = "male"
        elif v in ("f", "women", "woman"):
            v = "female"
        if v not in allowed:
            self.add("warning", "enum_invalid", fname, "%s %r is not one of %s; dropped" % (fname, v, ", ".join(sorted(allowed))))
            return ""
        return v

    def images(self, value: Any) -> List[str]:
        raw = value if isinstance(value, list) else [x for x in re.split(r"\s*[|,]\s*", _to_str(value)) if x]
        urls: List[str] = []
        for item in raw:
            u = self.url("additional_image_link", item)
            if u and u not in urls and u != self.out.get("image_link"):
                urls.append(u)
        cap = self.spec.max_additional_images
        if len(urls) > cap:
            self.add("warning", "additional_images_truncated", "additional_image_link",
                     "%d additional images; only the first %d are used" % (len(urls), cap))
            urls = urls[:cap]
        return urls

    # -- main
    def build(self) -> Dict[str, Any]:
        templates = self.cfg.fields_for(self.spec.name)
        raw = {name: render(t, self.ctx) for name, t in templates.items()}
        p = self.p
        if not p.currency:
            self.add("error", "currency_missing", "price", "no currency: set `currency` in the config or --currency")
        for name in [n for n in self.spec.field_order if n in raw] + [n for n in raw if n not in self.spec.field_order]:
            value = raw[name]
            if value in (None, "", [], ()):
                continue
            if name in ("price", "sale_price"):
                result: Any = self.money(name, value)
            elif name in ("link", "image_link"):
                result = self.url(name, value)
            elif name == "additional_image_link":
                result = self.images(value)
            elif name == "availability":
                result = self.availability(value)
            elif name == "gtin":
                result = self.gtin(value)
            elif name == "condition":
                result = _to_str(value).strip().lower()
                if result not in self.spec.conditions:
                    self.add("error", "condition_invalid", name, "condition %r must be one of %s" % (result, ", ".join(self.spec.conditions)))
                    result = ""
            elif name == "gender":
                result = self.enum(name, value, GENDERS)
            elif name == "age_group":
                result = self.enum(name, value, AGE_GROUPS)
            elif name == "inventory":
                result = _to_str(value) if _to_str(value).lstrip("-").isdigit() else ""
            else:
                result = self.text(name, value)
            if result not in ("", [], None):
                self.out[name] = result
        if "price" not in self.out and "price" not in self.flagged and p.extra.get("_price_raw"):
            self.add("error", "price_invalid", "price", "cannot parse price %r" % (p.extra["_price_raw"],))
        if "availability" not in self.out and "availability" not in self.flagged and p.extra.get("_availability_raw"):
            self.add("error", "availability_invalid", "availability",
                     "unrecognised availability %r" % (p.extra["_availability_raw"],))
        if "sale_price" not in self.out:
            self.out.pop("sale_price_effective_date", None)
        for code, msg in resolve_sale(p, self.cfg, self.now, self.tz)[2]:
            self.add("warning", code, "sale_price", msg)
        if self.spec.name == "google" and self.cfg.auto_identifier_exists:
            if not self.out.get("gtin") and not (self.out.get("brand") and self.out.get("mpn")):
                self.out["identifier_exists"] = "no"
        self._shipping()
        self._required()
        return self.out

    def _shipping(self) -> None:
        rows = []
        for row in self.cfg.shipping:
            amount, currency = split_money(row["price"])
            currency = (currency if currency and len(currency) == 3 else None) or self.p.currency
            if amount is None or not currency:
                continue
            rows.append({"country": row["country"], "service": row.get("service", ""),
                         "price": format_money(amount, currency)})
        if not rows:
            return
        if self.spec.name == "google":
            self.out["shipping"] = rows
        else:
            self.out["shipping"] = ",".join("%s::%s:%s" % (r["country"], r["service"], r["price"]) for r in rows)

    def _required(self) -> None:
        for name in self.spec.required:
            if not self.out.get(name) and name not in self.flagged:
                self.add("error", "missing_required", name, "required field %r is empty" % name)
        if self.spec.name == "google":
            cat = self.out.get("google_product_category", "") + " " + self.out.get("product_type", "")
            if cat.lower().startswith("apparel & accessories") or "1604" in cat.split():
                for name in ("gender", "age_group"):
                    if not self.out.get(name):
                        self.add("error", "missing_required", name, "%r is required for apparel products" % name)
                for name in ("color", "size"):
                    if not self.out.get(name):
                        self.add("warning", "missing_recommended", name, "%r is recommended for apparel products" % name)


def build_feeds(
    products: Sequence[Product],
    cfg: Config,
    channels: Sequence[str],
    now: Optional[datetime] = None,
    include_invalid: bool = False,
    image_checker: Optional[ImageChecker] = None,
) -> BuildResult:
    for ch in channels:
        if ch not in CHANNELS:
            raise ConfigError("unknown channel %r (choose from %s)" % (ch, ", ".join(CHANNELS)))
    now = now or datetime.now(timezone.utc)
    tz = parse_tz(cfg.timezone)
    items: List[Tuple[Product, Dict[str, Any]]] = []
    for p in products:
        prepare(p, cfg)
        items.append((p, product_context(p, cfg, now, tz)))
    items, filtered = apply_filters(items, cfg.filters)

    results: Dict[str, ChannelResult] = {}
    image_cache: Dict[str, Optional[str]] = {}
    for ch in channels:
        spec = CHANNELS[ch]
        res = ChannelResult(channel=ch)
        built: List[Tuple[Product, Dict[str, Any], List[FeedIssue]]] = []
        seen: Dict[str, int] = {}
        for p, ctx in items:
            b = _RecordBuilder(p, spec, cfg, ctx, now, tz)
            fields = b.build()
            pid = fields.get("id", "")
            if pid:
                seen[pid] = seen.get(pid, 0) + 1
                if seen[pid] > 1:
                    b.add("error", "duplicate_id", "id", "id %r appears more than once" % pid)
            built.append((p, fields, b.issues))
        if image_checker:
            wanted = sorted({u for _, f, i in built if not any(x.severity == "error" for x in i)
                             for u in [f.get("image_link", "")] + list(f.get("additional_image_link", [])) if u} - set(image_cache))
            if wanted:
                image_cache.update(image_checker(wanted))
            for p, fields, issues in built:
                for fname, urls in (("image_link", [fields.get("image_link", "")]),
                                    ("additional_image_link", fields.get("additional_image_link", []))):
                    for u in urls:
                        reason = image_cache.get(u) if u else None
                        if reason:
                            sev = cfg.image_severity if fname == "image_link" else "warning"
                            issues.append(FeedIssue(p.label, ch, sev, "image_unreachable", fname, "%s: %s" % (u, reason)))
        for p, fields, issues in built:
            res.issues.extend(issues)
            if any(i.severity == "error" for i in issues) and not include_invalid:
                res.invalid.append((p.label, "; ".join(i.message for i in issues if i.severity == "error")[:200]))
                continue
            res.records.append(Record(fields.get("id", p.label), fields))
        results[ch] = res
    return BuildResult(channels=results, filtered=filtered, total_input=len(products))
