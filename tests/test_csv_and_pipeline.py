from datetime import timedelta
from decimal import Decimal

import pytest

from conftest import NOW
from feed_builder.config import parse_config
from feed_builder.models import SourceError
from feed_builder.pipeline import build_feeds, normalize_availability
from feed_builder.sources.csv_source import CsvSource, build_column_map


def write_csv(tmp_path, text, name="p.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def read(tmp_path, text, **kw):
    return list(CsvSource(write_csv(tmp_path, text), **kw).fetch())


# ------------------------------------------------------------------ CSV source
def test_csv_aliases_and_basic_fields(tmp_path):
    (p,) = read(tmp_path, "sku,name,url,image,price,stock_status,vendor,ean\nA1,Shirt,/p/a,/i.jpg,\"1,299.00\",instock,Acme,4006381333931\n")
    assert (p.id, p.title, p.link, p.price, p.brand, p.gtin) == ("A1", "Shirt", "/p/a", Decimal("1299.00"), "Acme", "4006381333931")
    assert p.availability == "instock"  # normalised later by the pipeline


def test_csv_bom_and_header_normalisation(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿SKU,Product Name,Regular Price\nA1,Shirt,10\n".encode())
    (p,) = list(CsvSource(str(path)).fetch())
    assert p.title == "Shirt" and p.price == Decimal("10")


def test_csv_column_overrides(tmp_path):
    (p,) = read(tmp_path, "Code,Label,Cost\nA1,Shirt,5\n", column_overrides={"id": "Code", "title": "Label", "price": "Cost"})
    assert (p.id, p.title, p.price) == ("A1", "Shirt", Decimal("5"))


def test_csv_override_pointing_at_missing_header(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        read(tmp_path, "sku,name\nA,B\n", column_overrides={"price": "Cost"})


def test_csv_requires_id_or_title_column(tmp_path):
    with pytest.raises(SourceError, match="at least"):
        read(tmp_path, "foo,bar\n1,2\n")


def test_csv_missing_file():
    with pytest.raises(SourceError, match="cannot open"):
        list(CsvSource("/no/such/file.csv").fetch())


def test_csv_price_with_currency_and_sale_interval(tmp_path):
    text = "sku,name,price,sale_price,sale_price_effective_date\nA,B,1299.00 BDT,999,2026-03-01T00:00+06:00/2026-03-31T23:59+06:00\n"
    (p,) = read(tmp_path, text)
    assert p.currency == "BDT" and p.sale_price == Decimal("999")
    assert p.sale_start.utcoffset() == timedelta(hours=6) and p.sale_end.day == 31


def test_csv_separate_sale_dates_use_config_timezone(tmp_path):
    (p,) = read(tmp_path, "sku,name,price,sale_price,sale_start,sale_end\nA,B,10,8,2026-03-01,2026-03-31\n", tz="+06:00")
    assert p.sale_start.utcoffset() == timedelta(hours=6) and (p.sale_end.hour, p.sale_end.minute) == (23, 59)


def test_csv_invalid_price_is_recorded_not_fatal(tmp_path):
    (p,) = read(tmp_path, "sku,name,price\nA,B,call us\n")
    assert p.price is None and p.extra["_price_raw"] == "call us"


def test_csv_lists_categories_and_extra_columns(tmp_path):
    (p,) = read(tmp_path, "sku,name,images,categories,tags,Fabric Type\nA,B,u1|u2,Men|Shirts,x;y,linen\n")
    assert p.additional_images == ["u1", "u2"] and p.categories == ["Men", "Shirts"] and p.tags == ["x", "y"]
    assert p.category_slugs == ["men", "shirts"] and p.extra["fabric_type"] == "linen"


def test_csv_skips_blank_rows_and_labels_rows(tmp_path):
    products = read(tmp_path, "sku,name\nA,One\n,\n,Two\n")
    assert [p.title for p in products] == ["One", "Two"] and products[1].label == "row 4"


def test_csv_tsv_autodetected(tmp_path):
    path = write_csv(tmp_path, "sku\tname\nA\tB\n", name="p.tsv")
    assert list(CsvSource(path).fetch())[0].title == "B"


def test_csv_attributes_columns(tmp_path):
    (p,) = read(tmp_path, "sku,name,color,size,gender,age_group\nA,B,Red,M,female,adult\n")
    assert p.attributes == {"color": "Red", "size": "M", "gender": "female", "age_group": "adult"}


def test_build_column_map_prefers_overrides():
    m = build_column_map(["SKU", "Name", "Title"], {"title": "Name"})
    assert m["title"] == "Name" and m["id"] == "SKU"


# ------------------------------------------------------------------ availability mapping
@pytest.mark.parametrize("raw,expected", [
    ("instock", "in_stock"), ("In Stock", "in_stock"), ("outofstock", "out_of_stock"), ("sold-out", "out_of_stock"),
    ("onbackorder", "backorder"), ("Pre-Order", "preorder"), ("yes", "in_stock"), ("0", "out_of_stock"),
    ("available for order", "backorder"), ("maybe", ""), ("", ""),
])
def test_normalize_availability(raw, expected):
    assert normalize_availability(raw) == expected


def test_normalize_availability_custom_map():
    assert normalize_availability("ships soon", {"ships soon": "backorder"}) == "backorder"


# ------------------------------------------------------------------ pipeline
def build(cfg, products, channels=("google",), **kw):
    return build_feeds(products, cfg, list(channels), now=NOW, **kw)


def fields(result, channel="google", index=0):
    return result.channels[channel].records[index].fields


def codes(result, channel="google"):
    return {i.code for i in result.channels[channel].issues}


def test_basic_record_fields(cfg, product):
    r = build(cfg, [product()])
    f = fields(r)
    assert f["id"] == "SKU-1" and f["price"] == "1299.00 BDT" and f["availability"] == "in_stock"
    assert f["condition"] == "new" and f["brand"] == "Example Wear" and f["gtin"] == "4006381333931"
    assert r.channels["google"].issues == []


def test_price_formats_currency_per_config(product):
    usd = parse_config({"currency": "USD"})
    assert fields(build(usd, [product(price=Decimal("9.5"))]))["price"] == "9.50 USD"
    jpy = parse_config({"currency": "JPY"})
    assert fields(build(jpy, [product(price=Decimal("1500"))]))["price"] == "1500 JPY"


def test_product_currency_overrides_config(cfg, product):
    assert fields(build(cfg, [product(currency="usd")]))["price"] == "1299.00 USD"


def test_missing_currency_is_error(product):
    r = build(parse_config({}), [product()])
    assert "currency_missing" in codes(r) and not r.channels["google"].records


def test_availability_labels_per_channel(cfg, product):
    ps = [product(availability="backorder")]
    r = build(cfg, ps, channels=("google", "meta"))
    assert fields(r, "google")["availability"] == "backorder"
    assert fields(r, "meta")["availability"] == "available for order"
    r2 = build(cfg, [product(availability="in_stock")], channels=("meta",))
    assert fields(r2, "meta")["availability"] == "in stock"


def test_invalid_availability_rejects_product(cfg, product):
    r = build(cfg, [product(availability="perhaps")])
    assert "availability_invalid" in codes(r) and r.channels["google"].invalid


def test_availability_map_from_config(product):
    cfg = parse_config({"currency": "BDT", "availability_map": {"ships soon": "backorder"}})
    assert fields(build(cfg, [product(availability="ships soon")]))["availability"] == "backorder"


def test_sale_price_with_effective_dates(cfg, product):
    p = product(sale_price=Decimal("999"), sale_start=NOW - timedelta(days=1), sale_end=NOW + timedelta(days=10))
    f = fields(build(cfg, [p]))
    assert f["sale_price"] == "999.00 BDT"
    assert f["sale_price_effective_date"] == "2026-05-31T18:00+06:00/2026-06-11T18:00+06:00"


def test_sale_without_dates_has_no_interval(cfg, product):
    f = fields(build(cfg, [product(sale_price=Decimal("999"))]))
    assert "sale_price_effective_date" not in f and f["sale_price"] == "999.00 BDT"


def test_sale_with_only_end_date_starts_now(cfg, product):
    f = fields(build(cfg, [product(sale_price=Decimal("999"), sale_end=NOW + timedelta(days=2))]))
    assert f["sale_price_effective_date"].startswith("2026-06-01T18:00+06:00/")


def test_sale_with_only_start_warns(cfg, product):
    r = build(cfg, [product(sale_price=Decimal("999"), sale_start=NOW)])
    assert "sale_dates_incomplete" in codes(r) and "sale_price_effective_date" not in fields(r)


def test_expired_sale_dropped_with_warning(cfg, product):
    p = product(sale_price=Decimal("999"), sale_start=NOW - timedelta(days=30), sale_end=NOW - timedelta(days=1))
    r = build(cfg, [p])
    assert "sale_price" not in fields(r) and "sale_expired" in codes(r) and r.channels["google"].records


def test_expired_sale_kept_when_configured(product):
    cfg = parse_config({"currency": "BDT", "sale": {"drop_expired": False}})
    p = product(sale_price=Decimal("999"), sale_start=NOW - timedelta(days=30), sale_end=NOW - timedelta(days=1))
    assert "sale_price" in fields(build(cfg, [p]))


def test_sale_not_lower_than_price_dropped(cfg, product):
    r = build(cfg, [product(sale_price=Decimal("1299"))])
    assert "sale_price" not in fields(r) and "sale_price_not_lower" in codes(r)


def test_sale_end_before_start_dropped(cfg, product):
    p = product(sale_price=Decimal("999"), sale_start=NOW + timedelta(days=5), sale_end=NOW + timedelta(days=1))
    r = build(cfg, [p])
    assert "sale_price" not in fields(r) and "sale_dates_invalid" in codes(r)


def test_invalid_gtin_dropped_by_default(cfg, product):
    r = build(cfg, [product(gtin="4006381333932", mpn="")])
    assert "gtin" not in fields(r) and "gtin_invalid" in codes(r)
    assert fields(r)["identifier_exists"] == "no"


def test_invalid_gtin_error_policy(product):
    cfg = parse_config({"currency": "BDT", "gtin": {"on_invalid": "error"}})
    r = build(cfg, [product(gtin="123")])
    assert r.channels["google"].invalid and not r.channels["google"].records


def test_invalid_gtin_keep_policy(product):
    cfg = parse_config({"currency": "BDT", "gtin": {"on_invalid": "keep"}})
    assert fields(build(cfg, [product(gtin="123")]))["gtin"] == "123"


def test_identifier_exists_logic(cfg, product):
    assert "identifier_exists" not in fields(build(cfg, [product()]))
    assert fields(build(cfg, [product(gtin="")]))["identifier_exists"] == "no"
    assert "identifier_exists" not in fields(build(cfg, [product(gtin="", mpn="X-1")]))
    off = parse_config({"currency": "BDT", "identifier_exists": {"auto": False}})
    assert "identifier_exists" not in fields(build(off, [product(gtin="")]))


@pytest.mark.parametrize("missing", ["title", "description", "link", "image_link", "brand"])
def test_required_fields_reject_product(cfg, product, missing):
    r = build(cfg, [product(**{missing: ""})])
    issues = [i for i in r.channels["google"].issues if i.code == "missing_required"]
    assert [i.field for i in issues] == [missing] and not r.channels["google"].records


def test_missing_price_and_unparseable_price(cfg, product):
    assert "missing_required" in codes(build(cfg, [product(price=None)]))
    r = build(cfg, [product(price=None, extra={"_price_raw": "call us"})])
    assert "price_invalid" in codes(r) and "missing_required" not in codes(r)


def test_zero_price_rejected(cfg, product):
    assert "price_not_positive" in codes(build(cfg, [product(price=Decimal("0"))]))


def test_meta_requires_condition_and_description(cfg, product):
    r = build(cfg, [product(description="")], channels=("meta",))
    assert "missing_required" in codes(r, "meta")


def test_condition_validation(cfg, product):
    assert "condition_invalid" in codes(build(cfg, [product(condition="mint")]))
    assert fields(build(cfg, [product(condition="Refurbished")]))["condition"] == "refurbished"


def test_defaults_applied(product):
    cfg = parse_config({"currency": "BDT", "defaults": {"brand": "House", "condition": "used", "gender": "unisex"}})
    f = fields(build(cfg, [product(brand="")]))
    assert f["brand"] == "House" and f["condition"] == "used" and f["gender"] == "unisex"


def test_title_truncated_and_description_limit(cfg, product):
    r = build(cfg, [product(title="word " * 60, description="d " * 3000)])
    f = fields(r)
    assert len(f["title"]) <= 150 and f["title"].endswith("...") and len(f["description"]) <= 5000
    assert {"truncated"} <= codes(r)


def test_meta_title_limit_is_200(cfg, product):
    r = build(cfg, [product(title="x" * 190)], channels=("google", "meta"))
    assert len(fields(r, "google")["title"]) <= 150 and len(fields(r, "meta")["title"]) == 190


def test_limits_on_exceed_error(product):
    cfg = parse_config({"currency": "BDT", "limits": {"on_exceed": "error"}})
    r = build(cfg, [product(title="x" * 160)])
    assert "too_long" in codes(r) and not r.channels["google"].records


def test_html_stripped_from_title_and_description(cfg, product):
    f = fields(build(cfg, [product(title="<b>Shirt</b>", description="<p>Nice &amp; soft</p><script>x</script>")]))
    assert f["title"] == "Shirt" and "<" not in f["description"] and "&amp;" not in f["description"]


def test_html_kept_when_strip_disabled(product):
    cfg = parse_config({"currency": "BDT", "strip_html": False})
    assert "<b>" in fields(build(cfg, [product(title="<b>Shirt</b>")]))["title"]


def test_control_characters_removed(cfg, product):
    assert fields(build(cfg, [product(title="Shi\x0brt\x00 tee")]))["title"] == "Shirt tee"


def test_all_caps_title_warns(cfg, product):
    assert "title_all_caps" in codes(build(cfg, [product(title="LEATHER SATCHEL")]))


def test_relative_links_made_absolute(cfg, product):
    f = fields(build(cfg, [product(link="/product/a b", image_link="//cdn.example.com/i.jpg")]))
    assert f["link"] == "https://shop.example.com/product/a%20b" and f["image_link"] == "https://cdn.example.com/i.jpg"


def test_invalid_url_rejected(cfg, product):
    r = build(cfg, [product(image_link="ftp://x/i.jpg")])
    assert "url_invalid" in codes(r)
    no_base = parse_config({"currency": "BDT"})
    assert "url_invalid" in codes(build(no_base, [product(link="/relative")]))


def test_placeholder_and_svg_images(cfg, product):
    assert "image_placeholder" in codes(build(cfg, [product(image_link="https://x.com/woocommerce-placeholder.png")]))
    r = build(cfg, [product(image_link="https://x.com/logo.svg")])
    assert "image_format" in codes(r) and r.channels["google"].records


def test_additional_images_deduped_and_capped(cfg, product):
    extra = ["https://cdn.example.com/%d.jpg" % i for i in range(15)] + ["https://cdn.example.com/shirt.jpg", "https://cdn.example.com/1.jpg"]
    r = build(cfg, [product(additional_images=extra)], channels=("google", "meta"))
    assert len(fields(r, "google")["additional_image_link"]) == 10
    assert "https://cdn.example.com/shirt.jpg" not in fields(r, "google")["additional_image_link"]
    assert "additional_images_truncated" in codes(r, "google")
    assert len(fields(r, "meta")["additional_image_link"]) == 15


def test_enum_normalisation(cfg, product):
    p = product(attributes={"gender": "Women", "age_group": "grownups", "color": "Red"})
    r = build(cfg, [p])
    f = fields(r)
    assert f["gender"] == "female" and "age_group" not in f and "enum_invalid" in codes(r)


def test_apparel_requires_gender_and_age_group(product):
    cfg = parse_config({"currency": "BDT", "defaults": {"google_product_category": "Apparel & Accessories > Clothing"}})
    r = build(cfg, [product()])
    missing = {i.field for i in r.channels["google"].issues if i.code == "missing_required"}
    assert missing == {"gender", "age_group"} and "missing_recommended" in codes(r)
    ok = build(cfg, [product(attributes={"gender": "male", "age_group": "adult", "color": "Red", "size": "M"})])
    assert ok.channels["google"].issues == [] and ok.channels["google"].records


def test_variants_share_item_group_id(cfg, product):
    ps = [product(id="T-S", item_group_id="T", attributes={"size": "S"}), product(id="T-M", item_group_id="T", attributes={"size": "M"})]
    r = build(cfg, ps)
    assert [rec.fields["item_group_id"] for rec in r.channels["google"].records] == ["T", "T"]
    assert [rec.fields["size"] for rec in r.channels["google"].records] == ["S", "M"]


def test_duplicate_ids_rejected(cfg, product):
    r = build(cfg, [product(), product()])
    assert len(r.channels["google"].records) == 1 and "duplicate_id" in codes(r)


def test_id_too_long_google(cfg, product):
    assert "too_long" in codes(build(cfg, [product(id="x" * 60)]))


def test_include_invalid_keeps_errored_records(cfg, product):
    r = build(cfg, [product(brand="")], include_invalid=True)
    assert r.channels["google"].records and r.channels["google"].errors


def test_field_mapping_templates(product):
    cfg = parse_config({
        "currency": "BDT",
        "fields": {"title": "{brand} {title|upper}"},
        "meta": {"fields": {"id": "shop_{id}", "brand": "{extra.label|default:House}"}},
    })
    r = build(cfg, [product(extra={"label": "Special"})], channels=("google", "meta"))
    assert fields(r, "google")["title"] == "Example Wear LINEN SHIRT" and fields(r, "google")["id"] == "SKU-1"
    assert fields(r, "meta")["id"] == "shop_SKU-1" and fields(r, "meta")["brand"] == "Special"


def test_custom_fields_pass_through(product):
    cfg = parse_config({"currency": "BDT", "google": {"fields": {"material": "{attributes.fabric|default:cotton}"}}})
    assert fields(build(cfg, [product()]))["material"] == "cotton"


def test_custom_labels(product):
    cfg = parse_config({
        "currency": "BDT",
        "custom_labels": {
            0: {"default": "regular", "rules": [{"value": "sale", "when": {"field": "on_sale", "op": "eq", "value": True}}]},
            1: [{"value": "premium", "when": {"field": "price", "op": "gte", "value": 1000}}],
            2: [{"value": "shirts", "when": {"field": "categories", "op": "contains", "value": "Shirts"}}],
        },
    })
    sale = product(id="A", sale_price=Decimal("900"), sale_start=NOW - timedelta(days=1), sale_end=NOW + timedelta(days=1))
    plain = product(id="B", price=Decimal("500"), categories=["Hats"])
    r = build(cfg, [sale, plain])
    a, b = (rec.fields for rec in r.channels["google"].records)
    assert (a["custom_label_0"], a["custom_label_1"], a["custom_label_2"]) == ("sale", "premium", "shirts")
    assert b["custom_label_0"] == "regular" and "custom_label_1" not in b


def test_custom_label_future_sale_not_active(product):
    cfg = parse_config({"currency": "BDT", "custom_labels": {0: [{"value": "sale", "when": {"field": "on_sale", "op": "eq", "value": True}}]}})
    p = product(sale_price=Decimal("900"), sale_start=NOW + timedelta(days=3), sale_end=NOW + timedelta(days=9))
    assert "custom_label_0" not in fields(build(cfg, [p]))


def test_custom_label_truncated_to_100(product):
    cfg = parse_config({"currency": "BDT", "custom_labels": {0: {"default": "x" * 130}}})
    assert len(fields(build(cfg, [product()]))["custom_label_0"]) <= 100


# ------------------------------------------------------------------ filters
def filtered(cfg_extra, products):
    cfg = parse_config(dict({"currency": "BDT"}, filters=cfg_extra))
    r = build(cfg, products)
    return [rec.product_id for rec in r.channels["google"].records], dict(r.filtered)


def test_filter_exclude_out_of_stock(product):
    ids, why = filtered({"exclude_out_of_stock": True}, [product(id="A"), product(id="B", availability="out_of_stock"),
                                                         product(id="C", availability="backorder")])
    assert ids == ["A", "C"] and why == {"B": "out of stock"}


def test_filter_categories_by_name_or_slug(product):
    ps = [product(id="A", categories=["Shirts"], category_slugs=["shirts"]),
          product(id="B", categories=["Gift Cards"], category_slugs=["gift-cards"])]
    assert filtered({"exclude_categories": ["gift-cards"]}, ps)[0] == ["A"]
    assert filtered({"include_categories": ["SHIRTS"]}, ps)[0] == ["A"]


def test_filter_ids_and_price_bounds(product):
    ps = [product(id="A", price=Decimal("50")), product(id="B", price=Decimal("500")), product(id="C", price=Decimal("5000"))]
    assert filtered({"exclude_ids": ["A"]}, ps)[0] == ["B", "C"]
    assert filtered({"min_price": 100, "max_price": 1000}, ps)[0] == ["B"]


def test_filter_exclude_if_condition(product):
    ps = [product(id="A", title="Test product"), product(id="B")]
    assert filtered({"exclude_if": [{"field": "title", "op": "regex", "value": "^test"}]}, ps)[0] == ["B"]
