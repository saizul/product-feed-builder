import csv
import io
import json
import os
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest
import requests

from conftest import NOW
from fakes import ConnectionError_, FakeResponse, FakeSession
from feed_builder import cli
from feed_builder.config import parse_config
from feed_builder.images import check_images, check_one, is_private_host
from feed_builder.pipeline import build_feeds
from feed_builder.report import to_csv, to_dict, to_text, write_report
from feed_builder.state import Delta, compute_delta, hashes, load_state, save_state
from feed_builder.writers import G_NS, google_xml, meta_csv, meta_csv_columns, record_hash, write_if_changed

NS = {"g": G_NS}


def records(cfg, products, channel="google"):
    return build_feeds(products, cfg, [channel], now=NOW).channels[channel].records


# ------------------------------------------------------------------ Google XML
def test_google_xml_structure(cfg, product):
    root = ET.fromstring(google_xml(records(cfg, [product()]), cfg))
    assert root.tag == "rss" and root.get("version") == "2.0"
    item = root.find("channel/item")
    assert item.find("g:id", NS).text == "SKU-1"
    assert item.find("g:price", NS).text == "1299.00 BDT"
    assert item.find("g:availability", NS).text == "in_stock"
    assert root.find("channel/title").text == "Product feed"


def test_google_xml_declaration_and_namespace_prefix(cfg, product):
    data = google_xml(records(cfg, [product()]), cfg)
    assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b'xmlns:g="http://base.google.com/ns/1.0"' in data


def test_google_xml_escapes_special_characters(cfg, product):
    recs = records(cfg, [product(title="Tom & Jerry \"2\" Mug > 5", description="Fish & chips > all")])
    item = ET.fromstring(google_xml(recs, cfg)).find("channel/item")
    assert item.find("g:title", NS).text == 'Tom & Jerry "2" Mug > 5'
    assert b"&amp;" in google_xml(recs, cfg)


def test_google_xml_multiple_additional_images_and_unicode(cfg, product):
    p = product(title="লিনেন শার্ট", additional_images=["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"])
    item = ET.fromstring(google_xml(records(cfg, [p]), cfg)).find("channel/item")
    assert len(item.findall("g:additional_image_link", NS)) == 2
    assert item.find("g:title", NS).text == "লিনেন শার্ট"


def test_google_xml_shipping_block(product):
    cfg = parse_config({"currency": "BDT", "shipping": [{"country": "BD", "service": "Standard", "price": "60"}]})
    ship = ET.fromstring(google_xml(records(cfg, [product()]), cfg)).find("channel/item/g:shipping", NS)
    assert ship.find("g:country", NS).text == "BD" and ship.find("g:price", NS).text == "60.00 BDT"


def test_google_xml_is_deterministic(cfg, product):
    recs = records(cfg, [product(), product(id="B")])
    assert google_xml(recs, cfg) == google_xml(recs, cfg)


def test_google_xml_empty_feed_is_valid(cfg):
    root = ET.fromstring(google_xml([], cfg))
    assert root.findall("channel/item") == []


# ------------------------------------------------------------------ Meta CSV
def parse_csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def test_meta_csv_basic(cfg, product):
    rows = parse_csv(meta_csv(records(cfg, [product()], "meta"), cfg))
    assert rows[0]["id"] == "SKU-1" and rows[0]["price"] == "1299.00 BDT" and rows[0]["availability"] == "in stock"
    assert rows[0]["condition"] == "new" and rows[0]["brand"] == "Example Wear"


def test_meta_csv_required_columns_always_present(cfg):
    header = meta_csv(records(cfg, [], "meta"), cfg).decode().splitlines()[0].split(",")
    assert {"id", "title", "description", "availability", "condition", "price", "link", "image_link", "brand"} <= set(header)


def test_meta_csv_additional_images_comma_joined_and_quoted(cfg, product):
    p = product(additional_images=["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"])
    rows = parse_csv(meta_csv(records(cfg, [p], "meta"), cfg))
    assert rows[0]["additional_image_link"] == "https://cdn.example.com/a.jpg,https://cdn.example.com/b.jpg"


def test_meta_csv_commas_quotes_and_newlines_roundtrip(cfg, product):
    p = product(title='Shirt, "slim" fit', description="Line one\nline two")
    rows = parse_csv(meta_csv(records(cfg, [p], "meta"), cfg))
    assert rows[0]["title"] == 'Shirt, "slim" fit' and rows[0]["description"] == "Line one line two"


def test_meta_csv_inventory_and_shipping(product):
    cfg = parse_config({"currency": "BDT", "shipping": [{"country": "BD", "service": "Standard", "price": "60 BDT"}]})
    rows = parse_csv(meta_csv(records(cfg, [product(stock_quantity=12)], "meta"), cfg))
    assert rows[0]["inventory"] == "12" and rows[0]["shipping"] == "BD::Standard:60.00 BDT"


def test_meta_csv_columns_order(cfg, product):
    cols = meta_csv_columns(records(cfg, [product(attributes={"color": "Red"})], "meta"))
    assert cols.index("id") < cols.index("price") < cols.index("brand") and "color" in cols


def test_meta_csv_variant_item_group(cfg, product):
    rows = parse_csv(meta_csv(records(cfg, [product(id="A", item_group_id="G"), product(id="B", item_group_id="G")], "meta"), cfg))
    assert [r["item_group_id"] for r in rows] == ["G", "G"]


# ------------------------------------------------------------------ incremental / atomic writes
def test_write_if_changed(tmp_path):
    path = str(tmp_path / "sub" / "f.xml")
    assert write_if_changed(path, b"one") is True
    mtime = os.path.getmtime(path)
    assert write_if_changed(path, b"one") is False and os.path.getmtime(path) == mtime
    assert write_if_changed(path, b"two") is True
    assert not os.path.exists(path + ".tmp")


def test_record_hash_stable_and_sensitive():
    assert record_hash({"a": "1", "b": ["x"]}) == record_hash({"b": ["x"], "a": "1"})
    assert record_hash({"a": "1"}) != record_hash({"a": "2"})


def test_compute_delta(cfg, product):
    old = records(cfg, [product(id="A"), product(id="B"), product(id="C")])
    previous = hashes(old)
    new = records(cfg, [product(id="A"), product(id="B", price=Decimal("999")), product(id="D")])
    delta = compute_delta(previous, new)
    assert (delta.new, delta.changed, delta.unchanged, delta.removed) == (["D"], ["B"], ["A"], ["C"])
    assert delta.upserts == ["D", "B"]


def test_compute_delta_first_run_everything_new(cfg, product):
    assert compute_delta({}, records(cfg, [product(id="A")])).new == ["A"]
    assert isinstance(compute_delta({}, []), Delta)


def test_state_roundtrip_and_errors(tmp_path):
    path = str(tmp_path / "state.json")
    assert load_state(path) == {}
    save_state(path, {"google": {"A": "h"}})
    assert load_state(path) == {"google": {"A": "h"}}
    with open(path, "w") as fh:
        fh.write("{nope")
    with pytest.raises(Exception, match="cannot read state"):
        load_state(path)
    with open(path, "w") as fh:
        json.dump({"version": 99}, fh)
    with pytest.raises(Exception, match="unsupported"):
        load_state(path)


# ------------------------------------------------------------------ image checks
def test_is_private_host():
    for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "svc.internal"):
        assert is_private_host(host)
    assert not is_private_host("cdn.example.com") and not is_private_host("8.8.8.8")


def test_check_one_ok_and_status_errors():
    ok = FakeSession({"https://cdn.example.com/a.jpg": FakeResponse(headers={"Content-Type": "image/jpeg"})})
    assert check_one("https://cdn.example.com/a.jpg", ok, 5) is None
    missing = FakeSession({"https://cdn.example.com/a.jpg": FakeResponse(status=404)})
    assert check_one("https://cdn.example.com/a.jpg", missing, 5) == "HTTP 404"


def test_check_one_not_an_image():
    s = FakeSession({"https://cdn.example.com/a.jpg": FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"})})
    assert "not an image" in check_one("https://cdn.example.com/a.jpg", s, 5)


def test_check_one_head_not_allowed_falls_back_to_ranged_get():
    s = FakeSession({"https://cdn.example.com/a.jpg": [FakeResponse(status=405)]})
    s.routes["https://cdn.example.com/a.jpg"] = lambda params: FakeResponse(headers={"Content-Type": "image/png"})
    # first call is HEAD -> our route returns 200 directly; emulate 405 by swapping on method
    calls = []

    class Sess:
        def head(self, url, **kw):
            calls.append("HEAD")
            return FakeResponse(status=405)

        def get(self, url, **kw):
            calls.append("GET")
            assert kw["headers"]["Range"] == "bytes=0-0"
            return FakeResponse(headers={"Content-Type": "image/png"})

    assert check_one("https://cdn.example.com/a.jpg", Sess(), 5) is None
    assert calls == ["HEAD", "GET"]


def test_check_one_network_error_and_private_host_and_scheme():
    s = FakeSession({"https://cdn.example.com/a.jpg": ConnectionError_("down")})
    assert check_one("https://cdn.example.com/a.jpg", s, 5).startswith("request failed")
    assert "private" in check_one("http://127.0.0.1/a.jpg", s, 5)
    assert check_one("ftp://x/a.jpg", s, 5) == "unsupported scheme"
    assert s.calls and all(c[0] == "HEAD" for c in s.calls)


def test_check_images_batch():
    s = FakeSession({"https://a.example.com/1.jpg": FakeResponse(headers={"Content-Type": "image/jpeg"}),
                     "https://a.example.com/2.jpg": FakeResponse(status=404)})
    result = check_images(["https://a.example.com/1.jpg", "https://a.example.com/2.jpg"], session=s)
    assert result == {"https://a.example.com/1.jpg": None, "https://a.example.com/2.jpg": "HTTP 404"}
    assert check_images([]) == {}


def test_pipeline_rejects_product_with_broken_main_image(cfg, product):
    checker = lambda urls: {u: ("HTTP 404" if "bad" in u else None) for u in urls}  # noqa: E731
    ps = [product(id="A"), product(id="B", image_link="https://cdn.example.com/bad.jpg",
                                   additional_images=["https://cdn.example.com/bad2.jpg"])]
    r = build_feeds(ps, cfg, ["google"], now=NOW, image_checker=checker)
    ch = r.channels["google"]
    assert [x.product_id for x in ch.records] == ["A"] and ch.invalid[0][0] == "B"
    assert any(i.code == "image_unreachable" and i.field == "additional_image_link" and i.severity == "warning" for i in ch.issues)


def test_broken_additional_image_is_only_a_warning(cfg, product):
    checker = lambda urls: {u: ("HTTP 404" if "bad" in u else None) for u in urls}  # noqa: E731
    r = build_feeds([product(additional_images=["https://cdn.example.com/bad.jpg"])], cfg, ["google"], now=NOW, image_checker=checker)
    assert len(r.channels["google"].records) == 1


def test_image_severity_warning_keeps_product(product):
    cfg = parse_config({"currency": "BDT", "images": {"severity": "warning"}})
    checker = lambda urls: {u: "HTTP 404" for u in urls}  # noqa: E731
    r = build_feeds([product()], cfg, ["google"], now=NOW, image_checker=checker)
    assert len(r.channels["google"].records) == 1


def test_image_checker_runs_once_per_unique_url(cfg, product):
    seen = []

    def checker(urls):
        seen.extend(urls)
        return {u: None for u in urls}

    build_feeds([product(id="A"), product(id="B")], cfg, ["google", "meta"], now=NOW, image_checker=checker)
    assert seen == ["https://cdn.example.com/shirt.jpg"]


# ------------------------------------------------------------------ reports
def sample_result(cfg, product):
    return build_feeds([product(id="A"), product(id="B", brand=""), product(id="C", gtin="123")], cfg, ["google", "meta"], now=NOW)


def test_report_dict(cfg, product):
    d = to_dict(sample_result(cfg, product))
    g = d["channels"]["google"]
    assert d["products_read"] == 3 and g["products_in_feed"] == 2 and g["products_rejected"] == 1
    assert g["rejected"][0]["product_id"] == "B" and {i["code"] for i in g["issues"]} >= {"missing_required", "gtin_invalid"}


def test_report_csv_and_text(cfg, product):
    rows = list(csv.DictReader(io.StringIO(to_csv(sample_result(cfg, product)))))
    assert {"channel", "severity", "product_id", "code", "field", "message"} <= set(rows[0])
    text = to_text(sample_result(cfg, product))
    assert "[google]" in text and "ERROR" in text and "missing_required" in text


def test_report_csv_neutralises_formulas(cfg, product):
    r = build_feeds([product(brand="", title="=SUM(A1)")], cfg, ["google"], now=NOW)
    r.channels["google"].issues[0].message = "=cmd()"
    assert "'=cmd()" in to_csv(r)


def test_write_report_by_extension(cfg, product, tmp_path):
    r = sample_result(cfg, product)
    for name in ("r.json", "r.csv", "r.txt"):
        write_report(r, str(tmp_path / "x" / name))
        assert os.path.getsize(str(tmp_path / "x" / name)) > 0
    assert json.load(open(str(tmp_path / "x" / "r.json")))["tool"] == "feed-builder"


# ------------------------------------------------------------------ CLI
CSV_TEXT = (
    "sku,name,description,url,image,price,sale_price,sale_start,sale_end,stock_status,brand,ean,category\n"
    "A1,Linen Shirt,A shirt.,https://shop.example.com/p/a1,https://cdn.example.com/a1.jpg,1299,999,2026-05-01,2026-12-31,instock,Acme,4006381333931,Shirts\n"
    "A2,Wool Hat,A hat.,https://shop.example.com/p/a2,https://cdn.example.com/a2.jpg,500,,,,outofstock,Acme,,Hats\n"
    "A3,Broken,,https://shop.example.com/p/a3,https://cdn.example.com/a3.jpg,10,,,,instock,Acme,,Hats\n"
)


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "products.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return str(path)


def run(args):
    return cli.main(["build"] + args)


def test_cli_builds_both_feeds(csv_file, tmp_path):
    out = str(tmp_path / "out")
    assert run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--out", out, "--quiet", "--now", "2026-06-01T00:00:00Z"]) == 0
    root = ET.parse(os.path.join(out, "google-merchant-feed.xml")).getroot()
    ids = [e.text for e in root.findall("channel/item/g:id", NS)]
    assert ids == ["A1", "A2"]  # A3 rejected: description missing
    rows = parse_csv(open(os.path.join(out, "meta-catalog-feed.csv"), "rb").read())
    assert [r["id"] for r in rows] == ["A1", "A2"] and rows[1]["availability"] == "out of stock"
    report = json.load(open(os.path.join(out, "report.json")))
    assert report["channels"]["google"]["rejected"][0]["product_id"] == "A3"


def test_cli_single_channel_and_custom_report(csv_file, tmp_path):
    out = str(tmp_path / "out")
    report = str(tmp_path / "rep" / "issues.csv")
    run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--channels", "meta", "--out", out, "--report", report, "--quiet"])
    assert os.path.exists(os.path.join(out, "meta-catalog-feed.csv")) and not os.path.exists(os.path.join(out, "google-merchant-feed.xml"))
    assert os.path.exists(report)


def test_cli_strict_exit_code(csv_file, tmp_path):
    base = ["--source", "csv", "--input", csv_file, "--currency", "BDT", "--out", str(tmp_path / "o"), "--quiet"]
    assert run(base) == 0
    assert run(base + ["--strict"]) == 1


def test_cli_include_invalid(csv_file, tmp_path):
    out = str(tmp_path / "o")
    run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--out", out, "--include-invalid", "--quiet"])
    root = ET.parse(os.path.join(out, "google-merchant-feed.xml")).getroot()
    assert len(root.findall("channel/item")) == 3


def test_cli_filters_from_config(csv_file, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("currency: BDT\nfilters:\n  exclude_out_of_stock: true\n")
    out = str(tmp_path / "o")
    run(["--source", "csv", "--input", csv_file, "--config", str(cfg), "--out", out, "--quiet"])
    root = ET.parse(os.path.join(out, "google-merchant-feed.xml")).getroot()
    assert [e.text for e in root.findall("channel/item/g:id", NS)] == ["A1"]


def test_cli_errors_exit_2(csv_file, tmp_path, capsys):
    out = str(tmp_path / "o")
    assert run(["--source", "csv", "--out", out, "--quiet"]) == 2  # --input missing
    assert run(["--source", "csv", "--input", csv_file, "--out", out, "--quiet"]) == 2  # no currency
    assert run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--channels", "tiktok", "--out", out, "--quiet"]) == 2
    assert run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--config", "/nope.yaml", "--out", out, "--quiet"]) == 2
    assert run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--now", "yesterday", "--out", out, "--quiet"]) == 2


def test_cli_incremental_workflow(csv_file, tmp_path):
    out = str(tmp_path / "o")
    args = ["--source", "csv", "--input", csv_file, "--currency", "BDT", "--out", out, "--incremental", "--quiet", "--now", "2026-06-01T00:00:00Z"]
    run(args)
    delta1 = parse_csv(open(os.path.join(out, "meta-catalog-feed.delta.csv"), "rb").read())
    assert [r["id"] for r in delta1] == ["A1", "A2"]
    feed = os.path.join(out, "google-merchant-feed.xml")
    mtime = os.path.getmtime(feed)

    run(args)  # nothing changed
    assert parse_csv(open(os.path.join(out, "meta-catalog-feed.delta.csv"), "rb").read()) == []
    assert os.path.getmtime(feed) == mtime

    with open(csv_file, "w", encoding="utf-8") as fh:  # A1 price changes, A2 removed
        fh.write(CSV_TEXT.replace("1299", "1399").replace("A2,Wool Hat", "A9,Wool Hat"))
    run(args)
    delta3 = parse_csv(open(os.path.join(out, "meta-catalog-feed.delta.csv"), "rb").read())
    assert sorted(r["id"] for r in delta3) == ["A1", "A9"]
    assert open(os.path.join(out, "removed-meta.txt")).read().split() == ["A2"]
    state = json.load(open(os.path.join(out, ".feed-state.json")))
    assert set(state["channels"]) == {"google", "meta"}


def test_cli_check_images_uses_checker(csv_file, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "check_images", lambda urls, timeout=10: seen.extend(urls) or {u: ("HTTP 404" if "a1" in u else None) for u in urls})
    out = str(tmp_path / "o")
    run(["--source", "csv", "--input", csv_file, "--currency", "BDT", "--out", out, "--check-images", "--quiet"])
    root = ET.parse(os.path.join(out, "google-merchant-feed.xml")).getroot()
    assert [e.text for e in root.findall("channel/item/g:id", NS)] == ["A2"]
    assert any("a1.jpg" in u for u in seen)


def test_cli_woocommerce_via_env_and_mock(tmp_path, monkeypatch):
    from feed_builder.sources import woocommerce as woo
    from test_sources_api import STORE, WC, woo_simple

    session = FakeSession({WC + "/products": FakeResponse([woo_simple()], headers={"X-WP-TotalPages": "1"}),
                           WC + "/data/currencies/current": FakeResponse({"code": "BDT"})})
    real = woo.client_from_env
    monkeypatch.setattr(woo, "client_from_env", lambda: real(
        {"WC_STORE_URL": STORE, "WC_CONSUMER_KEY": "ck", "WC_CONSUMER_SECRET": "cs"}, session=session))
    out = str(tmp_path / "o")
    assert run(["--source", "woocommerce", "--out", out, "--channels", "google", "--quiet", "--now", "2026-06-01T00:00:00Z"]) == 0
    item = ET.parse(os.path.join(out, "google-merchant-feed.xml")).getroot().find("channel/item")
    assert item.find("g:price", NS).text == "1299.00 BDT" and item.find("g:sale_price", NS).text == "999.00 BDT"
    assert {c[0] for c in session.calls} == {"GET"}


def test_cli_woocommerce_missing_env(tmp_path, monkeypatch):
    for var in ("WC_STORE_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert run(["--source", "woocommerce", "--out", str(tmp_path), "--quiet"]) == 2


def test_cli_shopify_via_env_and_mock(tmp_path, monkeypatch):
    from feed_builder.sources import shopify as shp
    from test_sources_api import BASE, SHOP, shopify_product

    session = FakeSession({BASE + "/products.json": FakeResponse({"products": [shopify_product()]}),
                           BASE + "/shop.json": FakeResponse({"shop": {"currency": "BDT"}})})
    real = shp.client_from_env
    monkeypatch.setattr(shp, "client_from_env", lambda shop="": real({"SHOPIFY_ADMIN_TOKEN": "t"}, shop=shop or SHOP, session=session))
    out = str(tmp_path / "o")
    assert run(["--source", "shopify", "--shop", SHOP, "--store-url", "https://store.example.com", "--out", out,
                "--channels", "meta", "--quiet"]) == 0
    rows = parse_csv(open(os.path.join(out, "meta-catalog-feed.csv"), "rb").read())
    assert [r["id"] for r in rows] == ["W-BRN", "W-BLK", "503"] and rows[0]["sale_price"] == "2500.00 BDT"
    assert rows[2]["availability"] == "out of stock" and rows[0]["item_group_id"] == "1"


def test_cli_gtin_command(capsys):
    assert cli.main(["gtin", "4006381333931"]) == 0
    assert cli.main(["gtin", "4006381333931", "123"]) == 1
    out = capsys.readouterr().out
    assert "valid" in out and "invalid" in out


def test_cli_init_config(tmp_path, capsys):
    assert cli.main(["init-config"]) == 0
    assert "currency: BDT" in capsys.readouterr().out
    target = str(tmp_path / "c.yaml")
    assert cli.main(["init-config", "--output", target]) == 0
    from feed_builder.config import load_config
    assert load_config(target).currency == "BDT"


def test_cli_no_command_prints_help(capsys):
    assert cli.main([]) == 2


def test_read_only_session_helper_raises_on_write():
    with pytest.raises(AssertionError):
        FakeSession().post("https://x")


def test_requests_exception_type_available():
    assert issubclass(requests.ConnectionError, requests.RequestException)
