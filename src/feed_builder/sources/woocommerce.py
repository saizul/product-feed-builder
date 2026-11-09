"""WooCommerce REST API (read-only): products and variations."""
from __future__ import annotations

import os
from typing import Any, Dict, Iterator, Optional

from ..dates import parse_dt, parse_tz
from ..http import ApiClient, require_https
from ..models import Product, SourceError
from ..money import parse_money
from . import canonical_attribute, clean_list, split_attrs

STOCK_MAP = {"instock": "in_stock", "outofstock": "out_of_stock", "onbackorder": "backorder"}


def client_from_env(env: Optional[Dict[str, str]] = None, session: Any = None) -> tuple[ApiClient, str]:
    env = os.environ if env is None else env
    store = (env.get("WC_STORE_URL") or "").rstrip("/")
    key, secret = env.get("WC_CONSUMER_KEY"), env.get("WC_CONSUMER_SECRET")
    missing = [n for n, v in (("WC_STORE_URL", store), ("WC_CONSUMER_KEY", key), ("WC_CONSUMER_SECRET", secret)) if not v]
    if missing:
        raise SourceError("missing environment variable(s): %s" % ", ".join(missing))
    require_https(store)
    return ApiClient(auth=(key, secret), session=session), store  # type: ignore[arg-type]


class WooCommerceSource:
    def __init__(self, client: ApiClient, store_url: str, per_page: int = 100, include_variations: bool = True,
                 status: str = "publish", tz: str = "+00:00"):
        self.client = client
        self.base = store_url.rstrip("/") + "/wp-json/wc/v3"
        self.per_page = per_page
        self.include_variations = include_variations
        self.status = status
        self.tz = parse_tz("+00:00")  # WooCommerce *_gmt fields are UTC

    # -- paging
    def _pages(self, path: str, params: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        page = 1
        while True:
            resp = self.client.get(self.base + path, params=dict(params, per_page=self.per_page, page=page))
            data = resp.json()
            if not isinstance(data, list):
                raise SourceError("unexpected WooCommerce response for %s: %r" % (path, str(data)[:120]))
            yield from data
            total_pages = resp.headers.get("X-WP-TotalPages")
            if total_pages and total_pages.isdigit():
                if page >= int(total_pages):
                    return
            elif len(data) < self.per_page:
                return
            if not data:
                return
            page += 1

    def fetch_currency(self) -> str:
        try:
            data = self.client.get(self.base + "/data/currencies/current").json()
            return str(data.get("code", "")).upper()
        except SourceError:
            return ""

    def fetch(self) -> Iterator[Product]:
        for row in self._pages("/products", {"status": self.status}):
            if self.include_variations and row.get("type") == "variable" and row.get("variations"):
                yield from self._variations(row)
            else:
                yield self._product(row)

    # -- mapping
    def _common(self, row: Dict[str, Any]) -> Dict[str, Any]:
        images = [i.get("src", "") for i in row.get("images", []) if i.get("src")]
        brands = [b.get("name", "") for b in row.get("brands", []) or []]
        attrs: Dict[str, str] = {}
        extra: Dict[str, Any] = {}
        for a in row.get("attributes", []) or []:
            name = str(a.get("name", ""))
            options = clean_list(a.get("options") or ([a["option"]] if a.get("option") else []))
            if name.lower() == "brand" and options:
                brands.append(options[0])
            elif canonical_attribute(name) and len(options) == 1:
                attrs[canonical_attribute(name)] = options[0]
            elif options:
                extra["attr_" + name.lower().replace(" ", "_")] = ", ".join(options)
        cats = [c.get("name", "") for c in row.get("categories", []) or []]
        return {
            "images": images,
            "brand": clean_list(brands)[0] if clean_list(brands) else "",
            "attrs": attrs,
            "extra": extra,
            "categories": clean_list(cats),
            "slugs": clean_list([c.get("slug", "") for c in row.get("categories", []) or []]),
            "tags": clean_list([t.get("name", "") for t in row.get("tags", []) or []]),
        }

    def _prices(self, row: Dict[str, Any], p: Product) -> None:
        regular = parse_money(row.get("regular_price"))
        active = parse_money(row.get("price"))
        sale = parse_money(row.get("sale_price"))
        p.price = regular if regular is not None else active
        if sale is not None and p.price is not None:
            p.sale_price = sale
            p.sale_start = parse_dt(row.get("date_on_sale_from_gmt") or "", self.tz)
            p.sale_end = parse_dt(row.get("date_on_sale_to_gmt") or "", self.tz)
        if p.price is None and row.get("regular_price") not in (None, ""):
            p.extra["_price_raw"] = str(row.get("regular_price"))

    def _stock(self, row: Dict[str, Any], p: Product) -> None:
        p.availability = STOCK_MAP.get(str(row.get("stock_status", "")).lower(), "")
        qty = row.get("stock_quantity")
        p.stock_quantity = int(qty) if isinstance(qty, (int, float)) else None

    def _product(self, row: Dict[str, Any]) -> Product:
        c = self._common(row)
        p = Product(
            id=str(row.get("sku") or row.get("id", "")),
            title=str(row.get("name", "")),
            description=str(row.get("description") or row.get("short_description") or ""),
            link=str(row.get("permalink", "")),
            image_link=c["images"][0] if c["images"] else "",
            additional_images=c["images"][1:],
            brand=c["brand"],
            gtin=str(row.get("global_unique_id") or ""),
            sku=str(row.get("sku") or ""),
            categories=c["categories"],
            category_slugs=c["slugs"],
            product_type=c["categories"][0] if c["categories"] else "",
            tags=c["tags"],
            attributes=c["attrs"],
            weight=str(row.get("weight") or ""),
            extra=dict(c["extra"], woo_id=row.get("id")),
            source="woocommerce",
        )
        self._prices(row, p)
        self._stock(row, p)
        return p

    def _variations(self, parent: Dict[str, Any]) -> Iterator[Product]:
        pc = self._common(parent)
        group = str(parent.get("sku") or parent.get("id"))
        for v in self._pages("/products/%s/variations" % parent["id"], {}):
            pairs = {a.get("name", ""): a.get("option", "") for a in v.get("attributes", []) or []}
            options = clean_list(list(pairs.values()))
            image = (v.get("image") or {}).get("src") or (pc["images"][0] if pc["images"] else "")
            extra = dict(pc["extra"], woo_id=v.get("id"), woo_parent_id=parent.get("id"))
            p = Product(
                id=str(v.get("sku") or v.get("id", "")),
                title=" - ".join([str(parent.get("name", ""))] + ([" / ".join(options)] if options else [])),
                description=str(v.get("description") or parent.get("description") or parent.get("short_description") or ""),
                link=str(v.get("permalink") or parent.get("permalink", "")),
                image_link=image,
                additional_images=[i for i in pc["images"] if i != image],
                brand=pc["brand"],
                gtin=str(v.get("global_unique_id") or ""),
                sku=str(v.get("sku") or ""),
                item_group_id=group,
                categories=pc["categories"],
                category_slugs=pc["slugs"],
                product_type=pc["categories"][0] if pc["categories"] else "",
                tags=pc["tags"],
                attributes=dict(pc["attrs"], **split_attrs(pairs)),
                weight=str(v.get("weight") or parent.get("weight") or ""),
                extra=extra,
                source="woocommerce",
            )
            self._prices(v, p)
            self._stock(v, p)
            yield p

