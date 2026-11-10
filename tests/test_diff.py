import json
from decimal import Decimal

import pytest

from conftest import NOW, make_product
from feed_builder import cli
from feed_builder.diff import diff_feeds, parse_feed, to_dict, to_json, to_text
from feed_builder.models import FeedBuilderError
from feed_builder.pipeline import build_feeds
from feed_builder.writers import google_xml, meta_csv


def feeds(cfg, products):
    res = build_feeds(products, cfg, ["google", "meta"], now=NOW)
    return google_xml(res.channels["google"].records, cfg), meta_csv(res.channels["meta"].records, cfg)


def base_products():
    return [
        make_product(id="A", title="Alpha", link="https://shop.example.com/product/a/"),
        make_product(id="B", title="Beta", link="https://shop.example.com/product/b/"),
        make_product(id="C", title="Gamma", link="https://shop.example.com/product/c/"),
    ]


def test_identical_feeds_have_no_changes(cfg):
    xml, csv_ = feeds(cfg, base_products())
    for data in (xml, csv_):
        d = diff_feeds(parse_feed(data), parse_feed(data))
        assert not d.has_changes and d.unchanged == 3


@pytest.mark.parametrize("which", [0, 1], ids=["google", "meta"])
def test_added_removed_changed(cfg, which):
    old = feeds(cfg, base_products())[which]
    new_products = [
        make_product(id="A", title="Alpha v2", link="https://shop.example.com/product/a/", price=Decimal("999")),
        make_product(id="C", title="Gamma", link="https://shop.example.com/product/c/"),
        make_product(id="D", title="Delta", link="https://shop.example.com/product/d/"),
    ]
    new = feeds(cfg, new_products)[which]
    d = diff_feeds(parse_feed(old), parse_feed(new))
    assert d.added == ["D"] and d.removed == ["B"] and d.unchanged == 1
    assert set(d.changed) == {"A"}
    assert d.changed["A"]["title"] == ("Alpha", "Alpha v2")
    assert d.changed["A"]["price"] == ("1299.00 BDT", "999.00 BDT")
    assert d.field_counts() == {"price": 1, "title": 1}


def test_ignore_fields(cfg):
    old = feeds(cfg, [make_product(id="A")])[0]
    new = feeds(cfg, [make_product(id="A", price=Decimal("5"))])[0]
    d = diff_feeds(parse_feed(old), parse_feed(new), ignore=["price"])
    assert not d.has_changes


def test_field_added_or_removed_is_reported_as_change(cfg):
    old_items = parse_feed(feeds(cfg, [make_product(id="A")])[1])
    new_items = parse_feed(feeds(cfg, [make_product(id="A")])[1])
    new_items.items["A"]["color"] = "Red"
    d = diff_feeds(old_items, new_items)
    assert d.changed["A"]["color"] == (None, "Red")


def test_additional_images_compare_as_lists(cfg):
    one = make_product(id="A", additional_images=["https://cdn.example.com/1.jpg"])
    two = make_product(id="A", additional_images=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"])
    d = diff_feeds(parse_feed(feeds(cfg, [one])[0]), parse_feed(feeds(cfg, [two])[0]))
    assert d.changed["A"]["additional_image_link"] == (
        ["https://cdn.example.com/1.jpg"],
        ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
    )


def test_mixed_formats_rejected(cfg):
    xml, csv_ = feeds(cfg, base_products())
    with pytest.raises(FeedBuilderError, match="cannot compare"):
        diff_feeds(parse_feed(xml), parse_feed(csv_))


def test_invalid_inputs():
    with pytest.raises(FeedBuilderError, match="valid XML"):
        parse_feed(b"<rss><channel>")
    with pytest.raises(FeedBuilderError, match="id"):
        parse_feed(b"title,price\nx,1\n")
    with pytest.raises(FeedBuilderError, match="g:id"):
        parse_feed(b"<rss><channel><item><title>x</title></item></channel></rss>")


def test_duplicate_ids_use_first_and_are_reported():
    data = b"id,title\n1,First\n1,Second\n2,Other\n"
    feed = parse_feed(data)
    assert feed.items["1"]["title"] == "First" and feed.duplicates == ["1"]
    assert "duplicate id" in to_text(diff_feeds(feed, feed))


def test_bom_and_blank_cells_in_csv():
    feed = parse_feed("﻿id,title,color\n1,Shirt,\n".encode())
    assert feed.items["1"] == {"id": "1", "title": "Shirt"}


def test_text_report_caps_lists(cfg):
    old = feeds(cfg, [make_product(id="X")])[0]
    new = feeds(cfg, [make_product(id="X")] + [make_product(id="N%d" % i) for i in range(5)])[0]
    d = diff_feeds(parse_feed(old), parse_feed(new))
    text = to_text(d, limit=2)
    assert "5 added" in text and "... and 3 more" in text
    assert "... and" not in to_text(d, limit=0)


def test_json_report_shape(cfg):
    old = feeds(cfg, base_products())[0]
    new = feeds(cfg, base_products()[:2])[0]
    data = json.loads(to_json(diff_feeds(parse_feed(old), parse_feed(new))))
    assert data["summary"] == {"added": 0, "removed": 1, "changed": 0, "unchanged": 2, "changed_by_field": {}}
    assert data["removed"] == ["C"] and data["feed"] == "google"
    assert to_dict(diff_feeds(parse_feed(old), parse_feed(old)))["added"] == []


def test_cli_diff(cfg, tmp_path, capsys):
    (tmp_path / "old.xml").write_bytes(feeds(cfg, base_products())[0])
    (tmp_path / "new.xml").write_bytes(feeds(cfg, base_products()[:2])[0])
    old, new = str(tmp_path / "old.xml"), str(tmp_path / "new.xml")
    assert cli.main(["diff", old, new]) == 0
    assert "1 removed" in capsys.readouterr().out
    assert cli.main(["diff", old, new, "--exit-code"]) == 1
    assert cli.main(["diff", old, old, "--exit-code"]) == 0
    out = tmp_path / "diff.json"
    assert cli.main(["diff", old, new, "--format", "json", "--output", str(out)]) == 0
    assert json.loads(out.read_text())["summary"]["removed"] == 1


def test_cli_diff_errors(tmp_path):
    assert cli.main(["diff", str(tmp_path / "missing.xml"), str(tmp_path / "also-missing.xml")]) == 2
