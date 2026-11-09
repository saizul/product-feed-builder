from decimal import Decimal

import pytest
import yaml

from feed_builder.config import load_config, parse_config
from feed_builder.models import ConfigError
from feed_builder.rules import evaluate, validate_condition
from feed_builder.sample_config import SAMPLE_CONFIG
from feed_builder.templates import render, strip_html, truncate, validate_template

CTX = {
    "title": "Linen Shirt", "brand": "", "price": Decimal("1299"), "tags": ["a", "b"], "on_sale": True,
    "attributes": {"color": "Blue"}, "categories": ["Men", "Shirts"], "description": "<p>Hello&nbsp;<b>world</b></p>",
    "qty": 3, "sku": "ABC-1",
}


# ------------------------------------------------------------------ templates
def test_render_lone_placeholder_keeps_type():
    assert render("{price}", CTX) == Decimal("1299")
    assert render("{tags}", CTX) == ["a", "b"]


def test_render_interpolation():
    assert render("Buy {title} in {attributes.color}", CTX) == "Buy Linen Shirt in Blue"


def test_render_missing_value_is_empty():
    assert render("{nope}", CTX) is None
    assert render("x{nope}y", CTX) == "xy"


def test_render_literal_text_and_non_strings():
    assert render("plain", CTX) == "plain"
    assert render(5, CTX) == 5


@pytest.mark.parametrize("template,expected", [
    ("{title|upper}", "LINEN SHIRT"),
    ("{title|lower}", "linen shirt"),
    ("{title|truncate:5}", "Li..."),
    ("{brand|default:Acme}", "Acme"),
    ("{title|prefix:New }", "New Linen Shirt"),
    ("{title|suffix: !}", "Linen Shirt !"),
    ("{tags|join: / }", "a / b"),
    ("{title|replace:Linen:Cotton}", "Cotton Shirt"),
    ("{description|strip_html}", "Hello world"),
    ("{title|lower|upper}", "LINEN SHIRT"),
])
def test_filters(template, expected):
    assert render(template, CTX) == expected


def test_filter_prefix_skips_empty():
    assert render("{brand|prefix:by }", CTX) == ""


def test_unknown_filter_raises():
    with pytest.raises(ConfigError):
        render("{title|shout}", CTX)
    with pytest.raises(ConfigError):
        validate_template("{title|shout}")


def test_bad_filter_argument_raises():
    with pytest.raises(ConfigError):
        render("{title|truncate:abc}", CTX)


def test_strip_html_and_truncate_helpers():
    assert strip_html("<p>A&amp;B</p><br>C") == "A&B C"
    assert truncate("short", 10) == "short"
    out = truncate("the quick brown fox jumps over", 15)
    assert len(out) <= 15 and out.endswith("...")
    assert truncate("abcdefghij", 3) == "abc"


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize("cond,expected", [
    ({"field": "price", "op": "gte", "value": 1299}, True),
    ({"field": "price", "op": "gt", "value": 1299}, False),
    ({"field": "price", "op": "lt", "value": "2000"}, True),
    ({"field": "price", "op": "eq", "value": 1299}, True),
    ({"field": "title", "op": "eq", "value": "linen shirt"}, True),
    ({"field": "title", "op": "ne", "value": "other"}, True),
    ({"field": "title", "op": "contains", "value": "LINEN"}, True),
    ({"field": "categories", "op": "contains", "value": "shirts"}, True),
    ({"field": "title", "op": "in", "value": ["linen shirt", "x"]}, True),
    ({"field": "title", "op": "not_in", "value": ["x"]}, True),
    ({"field": "categories", "op": "intersects", "value": ["women", "men"]}, True),
    ({"field": "sku", "op": "regex", "value": r"^abc-\d"}, True),
    ({"field": "brand", "op": "empty"}, True),
    ({"field": "title", "op": "not_empty"}, True),
    ({"field": "on_sale", "op": "eq", "value": True}, True),
    ({"field": "on_sale", "op": "eq", "value": False}, False),
])
def test_evaluate_leaf(cond, expected):
    assert evaluate(cond, CTX) is expected


def test_evaluate_combinators():
    a = {"field": "price", "op": "gt", "value": 1000}
    b = {"field": "title", "op": "contains", "value": "nope"}
    assert evaluate({"all": [a, b]}, CTX) is False
    assert evaluate({"any": [a, b]}, CTX) is True
    assert evaluate({"not": b}, CTX) is True


def test_evaluate_numeric_on_missing_is_false():
    assert evaluate({"field": "nope", "op": "gt", "value": 1}, CTX) is False


@pytest.mark.parametrize("bad", [
    {}, {"field": "x"}, {"field": "x", "op": "wat", "value": 1}, {"field": "x", "op": "eq"},
    {"all": []}, {"field": "x", "op": "regex", "value": "("}, "string",
])
def test_validate_condition_rejects(bad):
    with pytest.raises(ConfigError):
        validate_condition(bad)


# ------------------------------------------------------------------ config
def test_sample_config_is_valid():
    cfg = parse_config(yaml.safe_load(SAMPLE_CONFIG))
    assert cfg.currency == "BDT" and cfg.filters["exclude_out_of_stock"] is True
    assert "custom_label_0" in cfg.label_rules and cfg.label_defaults["custom_label_0"] == "regular"


def test_fields_for_merges_defaults_common_and_channel():
    cfg = parse_config({"fields": {"title": "{title|upper}"}, "meta": {"fields": {"id": "m_{id}"}}})
    assert cfg.fields_for("google")["title"] == "{title|upper}"
    assert cfg.fields_for("meta")["id"] == "m_{id}"
    assert cfg.fields_for("google")["id"] == "{id}"
    assert cfg.fields_for("meta")["inventory"] == "{stock_quantity}"


@pytest.mark.parametrize("data,fragment", [
    ({"curency": "BDT"}, "unknown config key"),
    ({"currency": "TAKA"}, "3-letter"),
    ({"timezone": "Dhaka"}, "timezone"),
    ({"defaults": {"colour": "x"}}, "unknown defaults"),
    ({"availability_map": {"x": "maybe"}}, "availability_map"),
    ({"fields": {"title": "{title|bogus}"}}, "unknown template filter"),
    ({"google": {"titles": {}}}, "unknown key"),
    ({"custom_labels": {"7": []}}, "custom_label_0..4"),
    ({"custom_labels": {"0": [{"value": "x"}]}}, "'value' and 'when'"),
    ({"filters": {"exclude_stock": True}}, "unknown filters"),
    ({"filters": {"exclude_categories": "Shirts"}}, "must be a list"),
    ({"limits": {"on_exceed": "ignore"}}, "on_exceed"),
    ({"gtin": {"on_invalid": "explode"}}, "on_invalid"),
    ({"images": {"severity": "fatal"}}, "severity"),
    ({"shipping": [{"country": "BD"}]}, "country"),
    ({"shipping": "free"}, "list"),
])
def test_config_validation_errors(data, fragment):
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    assert fragment in str(exc.value)


def test_load_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.yaml"))
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed")
    with pytest.raises(ConfigError):
        load_config(str(bad))
    assert load_config(None).currency == ""


def test_config_label_shorthand_list():
    cfg = parse_config({"custom_labels": {0: [{"value": "x", "when": {"field": "price", "op": "gt", "value": 1}}]}})
    assert cfg.label_rules["custom_label_0"][0].value == "x"
