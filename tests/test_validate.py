import json
from pathlib import Path

import pytest

from conftest import NOW, make_product
from feed_builder import cli
from feed_builder.diff import parse_feed
from feed_builder.models import FeedBuilderError
from feed_builder.pipeline import build_feeds
from feed_builder.validate import to_csv, to_dict, to_json, to_text, validate_feed
from feed_builder.writers import google_xml, meta_csv

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "output"

META_HEADER = "id,title,description,availability,condition,price,link,image_link,brand"
META_OK = (
    "A,Linen Shirt,A breathable linen shirt.,in stock,new,1299.00 BDT,"
    "https://shop.example.com/a,https://cdn.example.com/a.jpg,Example Wear"
)


def meta(*rows, header=META_HEADER):
    return parse_feed(("\n".join((header,) + rows) + "\n").encode())


def google_item(**fields):
    defaults = dict(
        id="A",
        title="Linen Shirt",
        description="A breathable linen shirt.",
        link="https://shop.example.com/a",
        image_link="https://cdn.example.com/a.jpg",
        availability="in_stock",
        price="1299.00 BDT",
        brand="Example Wear",
        gtin="4006381333931",
        condition="new",
    )
    defaults.update(fields)
    return "<item>%s</item>" % "".join("<g:%s>%s</g:%s>" % (k, v, k) for k, v in defaults.items() if v is not None)


def google(*items):
    body = '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>%s</channel></rss>' % "".join(items)
    return parse_feed(body.encode())


def codes(result, severity=None):
    return sorted(i.code for i in result.issues if severity is None or i.severity == severity)


def test_feeds_built_by_the_tool_validate_clean(cfg):
    products = [make_product(id="A"), make_product(id="B", title="Cotton Tee", link="https://shop.example.com/b")]
    res = build_feeds(products, cfg, ["google", "meta"], now=NOW)
    for data in (google_xml(res.channels["google"].records, cfg), meta_csv(res.channels["meta"].records, cfg)):
        result = validate_feed(parse_feed(data), now=NOW)
        assert result.issues == [] and result.items == 2


@pytest.mark.parametrize("name", ["google-merchant-feed.xml", "meta-catalog-feed.csv"])
def test_example_outputs_validate_clean(name):
    result = validate_feed(parse_feed((EXAMPLES / name).read_bytes()), now=NOW)
    assert result.errors == [] and result.warnings == []


def test_missing_required_and_empty_feed():
    result = validate_feed(meta("B,Shirt,,in stock,new,10.00 USD,https://shop.example.com/b,,Brand"), now=NOW)
    missing = sorted(i.field for i in result.issues if i.code == "missing_required")
    assert missing == ["description", "image_link"]
    assert result.items_with_errors() == ["B"]
    empty = validate_feed(meta(), now=NOW)
    assert codes(empty) == ["feed_empty"]


def test_rows_without_id_and_duplicates_are_errors():
    result = validate_feed(meta(META_OK, META_OK, ",Orphan,x,in stock,new,1.00 USD,,,"), now=NOW)
    assert codes(result, "error") == ["duplicate_id", "missing_required"]
    assert any(i.product_id == "row 4" for i in result.issues)


def test_channel_vocabulary_is_enforced():
    result = validate_feed(google(google_item(availability="in stock", condition="mint")), now=NOW)
    assert codes(result, "error") == ["availability_invalid", "condition_invalid"]
    msg = next(i.message for i in result.issues if i.code == "availability_invalid")
    assert "'in_stock'" in msg
    ok = validate_feed(meta(META_OK), now=NOW)
    assert ok.issues == []


@pytest.mark.parametrize(
    "price,sale,code",
    [
        ("1299", None, "price_invalid"),
        ("1,299.00 BDT", None, "price_invalid"),
        ("0.00 BDT", None, "price_not_positive"),
        ("100.00 BDT", "150.00 BDT", "sale_price_not_lower"),
        ("100.00 BDT", "90.00 USD", "currency_mismatch"),
        ("100.00 BDT", "ninety", "price_invalid"),
    ],
)
def test_price_checks(price, sale, code):
    result = validate_feed(google(google_item(price=price, sale_price=sale)), now=NOW)
    assert code in codes(result, "error")


def test_valid_sale_passes():
    item = google_item(
        price="100.00 BDT",
        sale_price="90.00 BDT",
        sale_price_effective_date="2026-05-01T00:00+06:00/2026-07-01T00:00+06:00",
    )
    assert validate_feed(google(item), now=NOW).issues == []


def test_sale_date_checks():
    def check(interval, sale="90.00 BDT"):
        return codes(
            validate_feed(
                google(google_item(price="100.00 BDT", sale_price=sale, sale_price_effective_date=interval)), now=NOW
            )
        )

    assert check("next week") == ["sale_dates_invalid"]
    assert check("2026-07-01T00:00+06:00/2026-05-01T00:00+06:00") == ["sale_dates_invalid"]
    assert check("2026-01-01T00:00+06:00/2026-02-01T00:00+06:00") == ["sale_expired"]
    assert check("2026-05-01T00:00+06:00/2026-07-01T00:00+06:00", sale=None) == ["sale_dates_without_price"]


def test_length_limits_differ_per_channel():
    title = "x" * 180
    assert "too_long" in codes(validate_feed(google(google_item(title=title)), now=NOW), "error")
    row = META_OK.replace("Linen Shirt", title)
    assert "too_long" not in codes(validate_feed(meta(row), now=NOW))
    label = validate_feed(google(google_item(custom_label_0="y" * 101)), now=NOW)
    assert [i.field for i in label.errors] == ["custom_label_0"]


def test_url_and_image_checks():
    result = validate_feed(
        google(google_item(link="/product/a", image_link="https://cdn.example.com/placeholder.png")), now=NOW
    )
    assert codes(result, "error") == ["image_placeholder", "url_invalid"]
    svg = validate_feed(google(google_item(image_link="https://cdn.example.com/a.svg")), now=NOW)
    assert codes(svg) == ["image_format"]
    many = "".join(
        "<g:additional_image_link>https://cdn.example.com/%d.jpg</g:additional_image_link>" % i for i in range(12)
    )
    dup = "<g:additional_image_link>https://cdn.example.com/a.jpg</g:additional_image_link>"
    result = validate_feed(google(google_item().replace("</item>", many + dup + "</item>")), now=NOW)
    assert codes(result) == ["additional_images_truncated", "image_duplicated"]


def test_meta_additional_images_split_on_commas():
    header = META_HEADER + ",additional_image_link"
    row = META_OK + ',"https://cdn.example.com/1.jpg,ftp://cdn.example.com/2.jpg"'
    result = validate_feed(meta(row, header=header), now=NOW)
    assert codes(result) == ["url_invalid"] and result.issues[0].severity == "warning"


def test_identifier_checks():
    assert codes(validate_feed(google(google_item(gtin="4006381333932")), now=NOW)) == ["gtin_invalid"]
    assert codes(validate_feed(google(google_item(gtin=None)), now=NOW)) == ["missing_identifier"]
    assert validate_feed(google(google_item(gtin=None, identifier_exists="no")), now=NOW).issues == []
    assert validate_feed(google(google_item(gtin=None, mpn="LS-1")), now=NOW).issues == []
    assert codes(validate_feed(google(google_item(identifier_exists="no")), now=NOW)) == ["identifier_conflict"]
    assert codes(validate_feed(google(google_item(identifier_exists="maybe")), now=NOW)) == ["enum_invalid"]


def test_enums_and_inventory():
    result = validate_feed(google(google_item(gender="men", age_group="teen")), now=NOW)
    assert codes(result, "warning") == ["enum_invalid", "enum_invalid"]
    inv = validate_feed(meta(META_OK + ",lots", header=META_HEADER + ",inventory"), now=NOW)
    assert codes(inv) == ["inventory_invalid"]


def test_content_checks():
    result = validate_feed(
        google(google_item(title="LINEN SHIRT SALE", description="&lt;p&gt;Nice&lt;/p&gt;")), now=NOW
    )
    assert codes(result) == ["html_in_text", "title_all_caps"]
    same = validate_feed(google(google_item(description="Linen shirt")), now=NOW)
    assert codes(same) == ["description_same_as_title"]


def test_apparel_rules_only_for_google():
    item = google_item(google_product_category="Apparel &amp; Accessories &gt; Clothing")
    result = validate_feed(google(item), now=NOW)
    assert codes(result, "error") == ["missing_required", "missing_required"]
    assert codes(result, "warning") == ["missing_recommended", "missing_recommended"]
    complete = google_item(google_product_category="1604", gender="male", age_group="adult", color="Blue", size="M")
    assert validate_feed(google(complete), now=NOW).issues == []


def test_variant_groups():
    def variant(pid, **kw):
        return google_item(id=pid, item_group_id="G1", **kw)

    good = google(variant("A", color="Black", size="M"), variant("B", color="Black", size="L"))
    assert validate_feed(good, now=NOW).issues == []
    bad = google(
        variant("A", color="Black", size="M"),
        variant("B", color="black", size="M"),
        variant("C"),
        variant("D", color="Red", brand="Other Brand"),
    )
    result = validate_feed(bad, now=NOW)
    assert codes(result) == ["variant_attributes_missing", "variant_brand_mismatch", "variant_duplicate"]
    dup = next(i for i in result.issues if i.code == "variant_duplicate")
    assert dup.product_id == "B" and "A" in dup.message
    clash = google(google_item(id="G1"), variant("A", color="Black"))
    assert codes(validate_feed(clash, now=NOW)) == ["variant_group_id_conflict"]


def test_channel_override_and_errors():
    data = meta(META_OK.replace("in stock", "in_stock"))
    assert codes(validate_feed(data, now=NOW)) == ["availability_invalid"]
    as_google = validate_feed(data, channel="google", now=NOW)
    assert as_google.channel == "google" and "availability_invalid" not in codes(as_google)
    with pytest.raises(FeedBuilderError, match="only be validated as a google"):
        validate_feed(google(google_item()), channel="meta")
    with pytest.raises(FeedBuilderError, match="unknown channel"):
        validate_feed(data, channel="tiktok")


def test_reports():
    result = validate_feed(google(google_item(gtin=None), google_item(id="B", price="free")), now=NOW)
    data = to_dict(result)
    assert data["summary"] == {
        "items": 2,
        "items_with_errors": 1,
        "errors": 1,
        "warnings": 1,
        "by_code": {"missing_identifier": 1, "price_invalid": 1},
    }
    assert json.loads(to_json(result))["channel"] == "google"
    text = to_text(result)
    assert text.index("ERROR") < text.index("WARNING") and "By code:" in text
    assert "... and 1 more" in to_text(result, limit=1)
    assert to_csv(result).splitlines()[0] == "channel,severity,product_id,code,field,message"
    assert "No problems found." in to_text(validate_feed(google(google_item()), now=NOW))


def test_csv_report_escapes_formula_cells():
    result = validate_feed(meta(META_OK.replace("A,", "=HYPERLINK(1),", 1).replace("1299.00 BDT", "x")), now=NOW)
    assert "'=HYPERLINK(1)" in to_csv(result)


def test_cli_validate(tmp_path, capsys):
    good = tmp_path / "good.xml"
    good.write_text(
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>%s</channel></rss>' % google_item()
    )
    warn = tmp_path / "warn.xml"
    warn.write_text(
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>%s</channel></rss>'
        % google_item(gtin=None)
    )
    bad = tmp_path / "bad.csv"
    bad.write_text(META_HEADER + "\n" + META_OK.replace("in stock", "available") + "\n")
    assert cli.main(["validate", str(good)]) == 0
    assert "No problems found." in capsys.readouterr().out
    assert cli.main(["validate", str(warn)]) == 0
    assert cli.main(["validate", str(warn), "--strict"]) == 1
    assert cli.main(["validate", str(bad)]) == 1
    assert "availability_invalid" in capsys.readouterr().out
    out = tmp_path / "issues.json"
    assert cli.main(["validate", str(bad), "--format", "json", "--output", str(out)]) == 1
    assert json.loads(out.read_text())["summary"]["errors"] == 1
    assert cli.main(["validate", str(bad), "--format", "csv"]) == 1
    assert capsys.readouterr().out.startswith("channel,severity")
    expired = tmp_path / "sale.xml"
    expired.write_text(
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>%s</channel></rss>'
        % google_item(
            price="100.00 BDT",
            sale_price="90.00 BDT",
            sale_price_effective_date="2026-05-01T00:00+06:00/2026-07-01T00:00+06:00",
        )
    )
    assert cli.main(["validate", str(expired), "--now", "2026-06-01", "--strict"]) == 0
    assert cli.main(["validate", str(expired), "--now", "2026-08-01", "--strict"]) == 1


def test_cli_validate_errors(tmp_path):
    assert cli.main(["validate", str(tmp_path / "missing.xml")]) == 2
    (tmp_path / "f.xml").write_text("<rss><channel><item><g:id>")
    assert cli.main(["validate", str(tmp_path / "f.xml")]) == 2
    (tmp_path / "ok.csv").write_text(META_HEADER + "\n" + META_OK + "\n")
    assert cli.main(["validate", str(tmp_path / "ok.csv"), "--now", "soon"]) == 2
