from decimal import Decimal

import pytest

from fakes import ConnectionError_, FakeResponse, FakeSession, sleeper
from feed_builder.http import ApiClient, require_https
from feed_builder.models import SourceError
from feed_builder.sources import canonical_attribute, shopify, woocommerce

STORE = "https://shop.example.com"
WC = STORE + "/wp-json/wc/v3"


def woo_simple(pid=1, **over):
    row = {
        "id": pid, "name": "Linen Shirt", "type": "simple", "sku": "LS-%d" % pid, "permalink": STORE + "/product/ls/",
        "description": "<p>Linen.</p>", "short_description": "short", "regular_price": "1299", "sale_price": "999",
        "price": "999", "date_on_sale_from_gmt": "2026-05-01T00:00:00", "date_on_sale_to_gmt": "2026-06-30T23:59:59",
        "stock_status": "instock", "stock_quantity": 7, "weight": "0.4",
        "categories": [{"id": 5, "name": "Shirts", "slug": "shirts"}], "tags": [{"name": "summer"}],
        "images": [{"src": "https://cdn.example.com/a.jpg"}, {"src": "https://cdn.example.com/b.jpg"}],
        "attributes": [{"name": "Colour", "options": ["Blue"]}, {"name": "Brand", "options": ["Example Wear"]},
                       {"name": "Fabric", "options": ["Linen", "Cotton"]}],
        "global_unique_id": "4006381333931",
    }
    row.update(over)
    return row


def woo_client(routes):
    session = FakeSession(routes)
    sleeps, sleep = sleeper()
    return ApiClient(auth=("ck", "cs"), session=session, sleep=sleep), session, sleeps


# ------------------------------------------------------------------ http client
def test_require_https_blocks_plain_http():
    with pytest.raises(SourceError):
        require_https("http://shop.example.com")
    require_https("https://shop.example.com")
    require_https("http://localhost:8080")
    require_https("http://store.test")


def test_client_only_issues_get_requests():
    client, session, _ = woo_client({WC + "/products": FakeResponse([])})
    client.get(WC + "/products")
    assert {c[0] for c in session.calls} == {"GET"}
    assert not any(hasattr(client, m) for m in ("post", "put", "patch", "delete"))


def test_client_retries_429_with_retry_after():
    client, session, sleeps = woo_client({WC + "/p": [FakeResponse({}, 429, {"Retry-After": "3"}), FakeResponse([1])]})
    assert client.get(WC + "/p").json() == [1]
    assert sleeps == [3.0]


def test_client_retries_server_errors_then_raises():
    client, session, _ = woo_client({WC + "/p": FakeResponse({}, 503)})
    with pytest.raises(SourceError):
        client.get(WC + "/p")
    assert len(session.calls) == 4


def test_client_auth_failure_message():
    client, _, _ = woo_client({WC + "/p": FakeResponse({}, 401)})
    with pytest.raises(SourceError, match="authentication failed"):
        client.get(WC + "/p")


def test_client_network_error_is_wrapped():
    client, _, _ = woo_client({WC + "/p": ConnectionError_("boom")})
    client.max_retries = 1
    with pytest.raises(SourceError, match="failed"):
        client.get(WC + "/p")


def test_client_passes_credentials_to_session():
    client, session, _ = woo_client({WC + "/p": FakeResponse([])})
    client.get(WC + "/p")
    assert session.calls[0][3]["auth"] == ("ck", "cs")


# ------------------------------------------------------------------ env handling
def test_woo_env_missing():
    with pytest.raises(SourceError, match="WC_CONSUMER_KEY"):
        woocommerce.client_from_env({"WC_STORE_URL": STORE})


def test_woo_env_requires_https():
    with pytest.raises(SourceError, match="https"):
        woocommerce.client_from_env({"WC_STORE_URL": "http://shop.example.com", "WC_CONSUMER_KEY": "a", "WC_CONSUMER_SECRET": "b"})


def test_woo_env_ok():
    client, store = woocommerce.client_from_env({"WC_STORE_URL": STORE + "/", "WC_CONSUMER_KEY": "a", "WC_CONSUMER_SECRET": "b"})
    assert store == STORE and client.auth == ("a", "b")


def test_shopify_env_missing_and_invalid():
    with pytest.raises(SourceError, match="SHOPIFY_ADMIN_TOKEN"):
        shopify.client_from_env({"SHOPIFY_SHOP": "x.myshopify.com"})
    with pytest.raises(SourceError, match="myshopify"):
        shopify.client_from_env({"SHOPIFY_SHOP": "evil.example.com", "SHOPIFY_ADMIN_TOKEN": "t"})


def test_shopify_env_ok_strips_scheme():
    client, shop = shopify.client_from_env({"SHOPIFY_ADMIN_TOKEN": "t"}, shop="https://my-shop.myshopify.com/")
    assert shop == "my-shop.myshopify.com" and client.headers["X-Shopify-Access-Token"] == "t"


# ------------------------------------------------------------------ WooCommerce
def test_woo_simple_product_mapping():
    client, _, _ = woo_client({WC + "/products": FakeResponse([woo_simple()], headers={"X-WP-TotalPages": "1"})})
    (p,) = list(woocommerce.WooCommerceSource(client, STORE).fetch())
    assert (p.id, p.sku, p.title) == ("LS-1", "LS-1", "Linen Shirt")
    assert p.price == Decimal("1299") and p.sale_price == Decimal("999")
    assert p.sale_start.isoformat().startswith("2026-05-01T00:00:00")
    assert p.availability == "in_stock" and p.stock_quantity == 7
    assert p.image_link.endswith("a.jpg") and p.additional_images == ["https://cdn.example.com/b.jpg"]
    assert p.brand == "Example Wear" and p.attributes == {"color": "Blue"} and p.extra["attr_fabric"] == "Linen, Cotton"
    assert p.gtin == "4006381333931" and p.categories == ["Shirts"] and p.product_type == "Shirts"
    assert p.description == "<p>Linen.</p>"


def test_woo_pagination_via_total_pages_header():
    def route(params):
        page = params["page"]
        return FakeResponse([woo_simple(page)], headers={"X-WP-TotalPages": "3"})

    client, session, _ = woo_client({WC + "/products": route})
    products = list(woocommerce.WooCommerceSource(client, STORE, per_page=1).fetch())
    assert [p.id for p in products] == ["LS-1", "LS-2", "LS-3"]
    assert [c[2]["page"] for c in session.calls] == [1, 2, 3]


def test_woo_pagination_without_header_stops_on_short_page():
    def route(params):
        return FakeResponse([woo_simple(1), woo_simple(2)] if params["page"] == 1 else [woo_simple(3)])

    client, _, _ = woo_client({WC + "/products": route})
    assert len(list(woocommerce.WooCommerceSource(client, STORE, per_page=2).fetch())) == 3


def test_woo_variable_product_expands_variations():
    parent = woo_simple(10, type="variable", sku="TEE", variations=[11, 12], regular_price="", sale_price="", price="")
    variations = [
        {"id": 11, "sku": "TEE-S", "regular_price": "500", "sale_price": "", "stock_status": "instock",
         "attributes": [{"name": "Size", "option": "S"}], "permalink": STORE + "/product/tee/?attribute_size=S",
         "image": {"src": "https://cdn.example.com/s.jpg"}},
        {"id": 12, "sku": "", "regular_price": "550", "stock_status": "onbackorder",
         "attributes": [{"name": "pa_size", "option": "M"}, {"name": "Colour", "option": "Red"}], "image": {}},
    ]
    client, _, _ = woo_client({
        WC + "/products": FakeResponse([parent], headers={"X-WP-TotalPages": "1"}),
        WC + "/products/10/variations": FakeResponse(variations, headers={"X-WP-TotalPages": "1"}),
    })
    products = list(woocommerce.WooCommerceSource(client, STORE).fetch())
    assert [p.id for p in products] == ["TEE-S", "12"]
    assert {p.item_group_id for p in products} == {"TEE"}
    assert products[0].title == "Linen Shirt - S" and products[1].title == "Linen Shirt - M / Red"
    assert products[1].attributes["size"] == "M" and products[1].attributes["color"] == "Red"
    assert products[0].image_link.endswith("s.jpg") and products[1].image_link.endswith("a.jpg")
    assert products[1].availability == "backorder" and products[0].link.endswith("attribute_size=S")


def test_woo_variable_without_variations_falls_back_to_parent():
    parent = woo_simple(10, type="variable", variations=[])
    client, _, _ = woo_client({WC + "/products": FakeResponse([parent])})
    assert len(list(woocommerce.WooCommerceSource(client, STORE).fetch())) == 1


def test_woo_unparseable_price_is_recorded():
    client, _, _ = woo_client({WC + "/products": FakeResponse([woo_simple(regular_price="N/A", price="N/A", sale_price="")])})
    (p,) = list(woocommerce.WooCommerceSource(client, STORE).fetch())
    assert p.price is None and p.extra["_price_raw"] == "N/A"


def test_woo_unexpected_payload():
    client, _, _ = woo_client({WC + "/products": FakeResponse({"code": "rest_forbidden"})})
    with pytest.raises(SourceError, match="unexpected"):
        list(woocommerce.WooCommerceSource(client, STORE).fetch())


def test_woo_currency_lookup():
    client, _, _ = woo_client({WC + "/data/currencies/current": FakeResponse({"code": "bdt"})})
    assert woocommerce.WooCommerceSource(client, STORE).fetch_currency() == "BDT"
    client2, _, _ = woo_client({})
    assert woocommerce.WooCommerceSource(client2, STORE).fetch_currency() == ""


def test_woo_requests_are_read_only():
    client, session, _ = woo_client({WC + "/products": FakeResponse([woo_simple()])})
    list(woocommerce.WooCommerceSource(client, STORE).fetch())
    assert all(c[0] == "GET" for c in session.calls)


# ------------------------------------------------------------------ Shopify
SHOP = "my-shop.myshopify.com"
BASE = "https://%s/admin/api/%s" % (SHOP, shopify.DEFAULT_API_VERSION)


def shopify_product(pid=1, **over):
    row = {
        "id": pid, "title": "Leather Wallet", "handle": "leather-wallet", "vendor": "Example Store",
        "product_type": "Accessories", "tags": "gift, leather", "body_html": "<p>Slim.</p>",
        "options": [{"name": "Color", "position": 1}],
        "images": [{"id": 100, "src": "https://cdn.example.com/w1.jpg"}, {"id": 101, "src": "https://cdn.example.com/w2.jpg"}],
        "image": {"src": "https://cdn.example.com/w1.jpg"},
        "variants": [
            {"id": 501, "title": "Brown", "option1": "Brown", "price": "2500.00", "compare_at_price": "3200.00",
             "sku": "W-BRN", "barcode": "4006381333931", "inventory_management": "shopify", "inventory_quantity": 4,
             "inventory_policy": "deny", "image_id": 101, "weight": 0.25, "weight_unit": "kg"},
            {"id": 502, "title": "Black", "option1": "Black", "price": "2500.00", "compare_at_price": None, "sku": "W-BLK",
             "inventory_management": "shopify", "inventory_quantity": 0, "inventory_policy": "continue"},
            {"id": 503, "title": "Tan", "option1": "Tan", "price": "2400.00", "sku": "", "inventory_management": "shopify",
             "inventory_quantity": 0, "inventory_policy": "deny"},
        ],
    }
    row.update(over)
    return row


def test_shopify_variant_mapping():
    session = FakeSession({BASE + "/products.json": FakeResponse({"products": [shopify_product()]})})
    src = shopify.ShopifySource(ApiClient(session=session), SHOP, store_url="https://store.example.com")
    brown, black, tan = list(src.fetch())
    assert brown.id == "W-BRN" and brown.item_group_id == "1" and brown.title == "Leather Wallet - Brown"
    assert brown.price == Decimal("3200.00") and brown.sale_price == Decimal("2500.00")
    assert black.price == Decimal("2500.00") and black.sale_price is None
    assert brown.link == "https://store.example.com/products/leather-wallet?variant=501"
    assert brown.image_link.endswith("w2.jpg") and black.image_link.endswith("w1.jpg")
    assert brown.attributes == {"color": "Brown"} and brown.brand == "Example Store"
    assert brown.gtin == "4006381333931" and brown.weight == "0.25 kg"
    assert brown.availability == "in_stock" and brown.stock_quantity == 4
    assert black.availability == "backorder" and tan.availability == "out_of_stock"
    assert tan.id == "503" and brown.tags == ["gift", "leather"]


def test_shopify_default_title_single_variant():
    row = shopify_product(options=[{"name": "Title"}], variants=[
        {"id": 9, "title": "Default Title", "option1": "Default Title", "price": "10.00", "sku": "ONE"}])
    session = FakeSession({BASE + "/products.json": FakeResponse({"products": [row]})})
    (p,) = list(shopify.ShopifySource(ApiClient(session=session), SHOP).fetch())
    assert p.title == "Leather Wallet" and p.link == "https://%s/products/leather-wallet" % SHOP
    assert p.availability == "in_stock" and p.attributes == {}


def test_shopify_link_header_pagination():
    page2 = BASE + "/products.json?limit=250&page_info=abc"
    session = FakeSession({
        BASE + "/products.json": FakeResponse({"products": [shopify_product(1)]}, headers={"Link": '<%s>; rel="next"' % page2}),
        page2: FakeResponse({"products": [shopify_product(2)]}, headers={"Link": '<%s>; rel="previous"' % BASE}),
    })
    products = list(shopify.ShopifySource(ApiClient(session=session), SHOP).fetch())
    assert {p.item_group_id for p in products} == {"1", "2"}
    assert session.calls[1][2] is None  # next-page URL is used verbatim, without extra params
    assert all(c[0] == "GET" for c in session.calls)


def test_shopify_sends_token_header():
    session = FakeSession({BASE + "/products.json": FakeResponse({"products": []})})
    client, _ = shopify.client_from_env({"SHOPIFY_ADMIN_TOKEN": "tok"}, shop=SHOP, session=session)
    list(shopify.ShopifySource(client, SHOP).fetch())
    assert session.calls[0][3]["headers"]["X-Shopify-Access-Token"] == "tok"


def test_shopify_currency_and_bad_payload():
    session = FakeSession({BASE + "/shop.json": FakeResponse({"shop": {"currency": "BDT"}}),
                           BASE + "/products.json": FakeResponse({"errors": "x"})})
    src = shopify.ShopifySource(ApiClient(session=session), SHOP)
    assert src.fetch_currency() == "BDT"
    with pytest.raises(SourceError):
        list(src.fetch())


def test_canonical_attribute_synonyms():
    assert canonical_attribute("Colour") == "color"
    assert canonical_attribute("pa_size") == "size"
    assert canonical_attribute("Age Group") == "age_group"
    assert canonical_attribute("Fabric") == ""
