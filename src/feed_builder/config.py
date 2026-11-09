"""YAML configuration loading and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .channels import CHANNELS, CUSTOM_LABELS
from .dates import parse_tz
from .models import AVAILABILITY_VALUES, ConfigError
from .rules import validate_condition
from .templates import validate_template

TOP_LEVEL_KEYS = {
    "currency", "store_url", "timezone", "feed", "defaults", "availability_map", "fields", "google", "meta",
    "custom_labels", "filters", "limits", "strip_html", "sale", "gtin", "identifier_exists", "images", "shipping",
    "csv", "woocommerce", "shopify",
}
FILTER_KEYS = {
    "exclude_out_of_stock", "include_categories", "exclude_categories", "exclude_ids", "min_price", "max_price",
    "exclude_if",
}
DEFAULT_KEYS = {
    "condition", "brand", "google_product_category", "gender", "age_group", "color", "size", "material",
    "pattern", "mpn",
}


@dataclass
class LabelRule:
    value: str
    when: Dict[str, Any]


@dataclass
class Config:
    currency: str = ""
    store_url: str = ""
    timezone: str = "+00:00"
    feed_title: str = "Product feed"
    feed_link: str = ""
    feed_description: str = "Product feed"
    defaults: Dict[str, str] = field(default_factory=dict)
    availability_map: Dict[str, str] = field(default_factory=dict)
    common_fields: Dict[str, str] = field(default_factory=dict)
    channel_fields: Dict[str, Dict[str, str]] = field(default_factory=dict)
    label_rules: Dict[str, List[LabelRule]] = field(default_factory=dict)
    label_defaults: Dict[str, str] = field(default_factory=dict)
    filters: Dict[str, Any] = field(default_factory=dict)
    on_exceed: str = "truncate"
    strip_html: bool = True
    drop_expired_sales: bool = True
    invalid_gtin: str = "drop"
    auto_identifier_exists: bool = True
    image_check: bool = False
    image_timeout: float = 10.0
    image_severity: str = "error"
    shipping: List[Dict[str, str]] = field(default_factory=list)
    csv_columns: Dict[str, str] = field(default_factory=dict)
    csv_delimiter: str = ","
    woo: Dict[str, Any] = field(default_factory=dict)
    shopify: Dict[str, Any] = field(default_factory=dict)

    def fields_for(self, channel: str) -> Dict[str, str]:
        merged = dict(CHANNELS[channel].default_fields)
        merged.update(self.common_fields)
        merged.update(self.channel_fields.get(channel, {}))
        return merged


def _mapping(value: Any, where: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % where)
    return value


def _fields(value: Any, where: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, template in _mapping(value, where).items():
        if template is None:
            out[str(key)] = ""
            continue
        template = str(template)
        validate_template(template)
        out[str(key)] = template
    return out


def parse_config(data: Optional[Dict[str, Any]]) -> Config:
    data = _mapping(data, "config")
    unknown = sorted(set(data) - TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigError("unknown config key(s): %s" % ", ".join(unknown))
    cfg = Config()
    cfg.currency = str(data.get("currency") or "").upper()
    if cfg.currency and (len(cfg.currency) != 3 or not cfg.currency.isalpha()):
        raise ConfigError("currency must be a 3-letter ISO 4217 code (got %r)" % data.get("currency"))
    cfg.store_url = str(data.get("store_url") or "").rstrip("/")
    cfg.timezone = str(data.get("timezone") or "+00:00")
    try:
        parse_tz(cfg.timezone)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    feed = _mapping(data.get("feed"), "feed")
    cfg.feed_title = str(feed.get("title") or cfg.feed_title)
    cfg.feed_link = str(feed.get("link") or cfg.store_url)
    cfg.feed_description = str(feed.get("description") or cfg.feed_description)

    defaults = _mapping(data.get("defaults"), "defaults")
    bad = sorted(set(defaults) - DEFAULT_KEYS)
    if bad:
        raise ConfigError("unknown defaults key(s): %s (allowed: %s)" % (", ".join(bad), ", ".join(sorted(DEFAULT_KEYS))))
    cfg.defaults = {k: str(v) for k, v in defaults.items() if v not in (None, "")}

    amap = _mapping(data.get("availability_map"), "availability_map")
    for src, dst in amap.items():
        if dst not in AVAILABILITY_VALUES:
            raise ConfigError("availability_map: %r maps to %r, expected one of %s" % (src, dst, ", ".join(AVAILABILITY_VALUES)))
    cfg.availability_map = {str(k).strip().lower(): str(v) for k, v in amap.items()}

    cfg.common_fields = _fields(data.get("fields"), "fields")
    for ch in CHANNELS:
        section = _mapping(data.get(ch), ch)
        extra_keys = sorted(set(section) - {"fields"})
        if extra_keys:
            raise ConfigError("unknown key(s) under %s: %s (expected 'fields')" % (ch, ", ".join(extra_keys)))
        cfg.channel_fields[ch] = _fields(section.get("fields"), ch + ".fields")

    _parse_labels(cfg, _mapping(data.get("custom_labels"), "custom_labels"))
    _parse_filters(cfg, _mapping(data.get("filters"), "filters"))

    limits = _mapping(data.get("limits"), "limits")
    cfg.on_exceed = str(limits.get("on_exceed", "truncate"))
    if cfg.on_exceed not in ("truncate", "error"):
        raise ConfigError("limits.on_exceed must be 'truncate' or 'error'")
    cfg.strip_html = bool(data.get("strip_html", True))

    sale = _mapping(data.get("sale"), "sale")
    cfg.drop_expired_sales = bool(sale.get("drop_expired", True))

    gtin = _mapping(data.get("gtin"), "gtin")
    cfg.invalid_gtin = str(gtin.get("on_invalid", "drop"))
    if cfg.invalid_gtin not in ("drop", "error", "keep"):
        raise ConfigError("gtin.on_invalid must be drop, error or keep")
    cfg.auto_identifier_exists = bool(_mapping(data.get("identifier_exists"), "identifier_exists").get("auto", True))

    images = _mapping(data.get("images"), "images")
    cfg.image_check = bool(images.get("check", False))
    cfg.image_timeout = float(images.get("timeout", 10))
    cfg.image_severity = str(images.get("severity", "error"))
    if cfg.image_severity not in ("error", "warning"):
        raise ConfigError("images.severity must be error or warning")

    shipping = data.get("shipping") or []
    if not isinstance(shipping, list):
        raise ConfigError("shipping must be a list")
    for row in shipping:
        if not isinstance(row, dict) or "country" not in row or "price" not in row:
            raise ConfigError("each shipping entry needs 'country' and 'price'")
    cfg.shipping = [{k: str(v) for k, v in row.items()} for row in shipping]

    csv_cfg = _mapping(data.get("csv"), "csv")
    cfg.csv_columns = {str(k): str(v) for k, v in _mapping(csv_cfg.get("columns"), "csv.columns").items()}
    cfg.csv_delimiter = str(csv_cfg.get("delimiter", ","))
    cfg.woo = _mapping(data.get("woocommerce"), "woocommerce")
    cfg.shopify = _mapping(data.get("shopify"), "shopify")
    return cfg


def _parse_labels(cfg: Config, labels: Dict[Any, Any]) -> None:
    for key, spec in labels.items():
        name = key if str(key).startswith("custom_label_") else "custom_label_%s" % key
        if name not in CUSTOM_LABELS:
            raise ConfigError("custom_labels: %r is not custom_label_0..4" % key)
        rules_raw = spec
        if isinstance(spec, dict):
            rules_raw = spec.get("rules", [])
            if spec.get("default") not in (None, ""):
                cfg.label_defaults[name] = str(spec["default"])
        if not isinstance(rules_raw, list):
            raise ConfigError("custom_labels.%s needs a list of rules" % key)
        rules = []
        for rule in rules_raw:
            if not isinstance(rule, dict) or "value" not in rule or "when" not in rule:
                raise ConfigError("custom_labels.%s: each rule needs 'value' and 'when'" % key)
            validate_condition(rule["when"], "custom_labels.%s" % key)
            rules.append(LabelRule(str(rule["value"]), rule["when"]))
        cfg.label_rules[name] = rules


def _parse_filters(cfg: Config, filters: Dict[str, Any]) -> None:
    unknown = sorted(set(filters) - FILTER_KEYS)
    if unknown:
        raise ConfigError("unknown filters key(s): %s" % ", ".join(unknown))
    for key in ("include_categories", "exclude_categories", "exclude_ids"):
        if key in filters and not isinstance(filters[key], list):
            raise ConfigError("filters.%s must be a list" % key)
    for cond in filters.get("exclude_if", []) or []:
        validate_condition(cond, "filters.exclude_if")
    cfg.filters = dict(filters)


def load_config(path: Optional[str]) -> Config:
    if not path:
        return parse_config({})
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc)) from exc
    except yaml.YAMLError as exc:
        raise ConfigError("invalid YAML in %s: %s" % (path, exc)) from exc
    return parse_config(data or {})
