"""Shopify Admin REST API (read-only): products and variants."""
from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any, Dict, Iterator, Optional, Tuple

from ..http import ApiClient
from ..models import Product, SourceError
from ..money import parse_money
from . import clean_list, split_attrs

DEFAULT_API_VERSION = "2024-10"
_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")


def client_from_env(env: Optional[Dict[str, str]] = None, shop: str = "", session: Any = None) -> Tuple[ApiClient, str]:
    env = os.environ if env is None else env
    shop = (shop or env.get("SHOPIFY_SHOP") or "").strip().lower()
    token = env.get("SHOPIFY_ADMIN_TOKEN")
    missing = [n for n, v in (("SHOPIFY_SHOP (or --shop)", shop), ("SHOPIFY_ADMIN_TOKEN", token)) if not v]
    if missing:
        raise SourceError("missing environment variable(s): %s" % ", ".join(missing))
    shop = re.sub(r"^https?://", "", shop).rstrip("/")
    if not _SHOP_RE.match(shop):
        raise SourceError("shop must look like your-store.myshopify.com (got %r)" % shop)
    return ApiClient(headers={"X-Shopify-Access-Token": token or ""}, session=session), shop


class ShopifySource:
    def __init__(self, client: ApiClient, shop: str, store_url: str = "", api_version: str = DEFAULT_API_VERSION,
                 status: str = "active", limit: int = 250):
        self.client = client
        self.shop = shop
        self.store_url = (store_url or "https://%s" % shop).rstrip("/")
        self.base = "https://%s/admin/api/%s" % (shop, api_version)
        self.status = status
        self.limit = limit

    def fetch_currency(self) -> str:
        try:
            data = self.client.get(self.base + "/shop.json").json()
            return str((data.get("shop") or {}).get("currency", "")).upper()
        except SourceError:
            return ""

    def _pages(self) -> Iterator[Dict[str, Any]]:
        url: Optional[str] = self.base + "/products.json"
        params: Optional[Dict[str, Any]] = {"limit": self.limit, "status": self.status}
        while url:
            resp = self.client.get(url, params=params)
            data = resp.json()
            if not isinstance(data, dict) or "products" not in data:
                raise SourceError("unexpected Shopify response: %r" % str(data)[:120])
            yield from data["products"]
            m = _NEXT_RE.search(resp.headers.get("Link", "") or "")
            url, params = (m.group(1), None) if m else (None, None)

    def fetch(self) -> Iterator[Product]:
        for row in self._pages():
            yield from self._variants(row)

    @staticmethod
    def _availability(variant: Dict[str, Any]) -> str:
        if not variant.get("inventory_management"):
            return "in_stock"
        qty = variant.get("inventory_quantity")
        if isinstance(qty, (int, float)) and qty > 0:
            return "in_stock"
        return "backorder" if variant.get("inventory_policy") == "continue" else "out_of_stock"

    def _variants(self, row: Dict[str, Any]) -> Iterator[Product]:
        images = row.get("images") or []
        default_img = (row.get("image") or {}).get("src") or (images[0].get("src") if images else "")
        by_id = {i.get("id"): i.get("src", "") for i in images}
        option_names = [o.get("name", "") for o in row.get("options", []) or []]
        variants = row.get("variants") or []
        single_default = len(variants) == 1 and str(variants[0].get("title", "")).lower() == "default title"
        tags = clean_list(str(row.get("tags", "")).split(","))
        for v in variants:
            pairs = {name: v.get("option%d" % (i + 1)) or "" for i, name in enumerate(option_names)}
            pairs = {k: val for k, val in pairs.items() if val and val != "Default Title"}
            image = by_id.get(v.get("image_id")) or default_img
            price = parse_money(v.get("price"))
            compare = parse_money(v.get("compare_at_price"))
            p = Product(
                id=str(v.get("sku") or v.get("id", "")),
                title=str(row.get("title", "")) if single_default or not pairs
                else "%s - %s" % (row.get("title", ""), " / ".join(pairs.values())),
                description=str(row.get("body_html") or ""),
                link="%s/products/%s%s" % (self.store_url, row.get("handle", ""), "" if single_default else "?variant=%s" % v.get("id")),
                image_link=image or "",
                additional_images=[i.get("src", "") for i in images if i.get("src") and i.get("src") != image],
                brand=str(row.get("vendor") or ""),
                gtin=str(v.get("barcode") or ""),
                sku=str(v.get("sku") or ""),
                item_group_id=str(row.get("id", "")),
                product_type=str(row.get("product_type") or ""),
                categories=clean_list([row.get("product_type", "")]),
                category_slugs=clean_list([str(row.get("product_type", "")).lower().replace(" ", "-")]),
                tags=tags,
                attributes=split_attrs(pairs),
                weight=_weight(v),
                stock_quantity=v.get("inventory_quantity") if v.get("inventory_management") else None,
                availability=self._availability(v),
                extra={"shopify_product_id": row.get("id"), "shopify_variant_id": v.get("id"), "handle": row.get("handle")},
                source="shopify",
            )
            if price is None and v.get("price") not in (None, ""):
                p.extra["_price_raw"] = str(v.get("price"))
            if compare is not None and price is not None and compare > price:
                p.price, p.sale_price = compare, price
            else:
                p.price = price
            yield p


def _weight(variant: Dict[str, Any]) -> str:
    w = variant.get("weight")
    if not w:
        return ""
    unit = {"g": "g", "kg": "kg", "oz": "oz", "lb": "lb"}.get(str(variant.get("weight_unit", "")).lower(), "")
    return ("%s %s" % (Decimal(str(w)).normalize(), unit)).strip() if unit else ""
