"""Channel specifications (Google Merchant Center, Meta catalog).

Limits and required fields mirror the public feed specifications at the time of
writing. Feed specs change; verify against the official documentation:
  Google: https://support.google.com/merchants/answer/7052112
  Meta:   https://www.facebook.com/business/help/120325381656392
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

CUSTOM_LABELS = ["custom_label_%d" % i for i in range(5)]

_COMMON_DEFAULTS = {
    "id": "{id}",
    "title": "{title}",
    "description": "{description}",
    "link": "{link}",
    "image_link": "{image_link}",
    "additional_image_link": "{additional_images}",
    "availability": "{availability}",
    "price": "{price}",
    "sale_price": "{sale_price}",
    "sale_price_effective_date": "{sale_dates}",
    "brand": "{brand}",
    "gtin": "{gtin}",
    "mpn": "{mpn}",
    "condition": "{condition}",
    "item_group_id": "{item_group_id}",
    "google_product_category": "{google_product_category}",
    "product_type": "{product_type}",
    "color": "{attributes.color}",
    "size": "{attributes.size}",
    "material": "{attributes.material}",
    "pattern": "{attributes.pattern}",
    "gender": "{attributes.gender}",
    "age_group": "{attributes.age_group}",
    "shipping_weight": "{weight}",
    "custom_label_0": "{custom_label_0}",
    "custom_label_1": "{custom_label_1}",
    "custom_label_2": "{custom_label_2}",
    "custom_label_3": "{custom_label_3}",
    "custom_label_4": "{custom_label_4}",
}


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    label: str
    file_name: str
    delta_file_name: str
    field_order: List[str]
    default_fields: Dict[str, str]
    required: List[str]
    limits: Dict[str, int]
    availability_labels: Dict[str, str]
    max_additional_images: int
    conditions: List[str] = field(default_factory=lambda: ["new", "refurbished", "used"])


GOOGLE = ChannelSpec(
    name="google",
    label="Google Merchant Center",
    file_name="google-merchant-feed.xml",
    delta_file_name="google-merchant-feed.delta.xml",
    field_order=[
        "id", "title", "description", "link", "image_link", "additional_image_link", "availability", "price",
        "sale_price", "sale_price_effective_date", "brand", "gtin", "mpn", "identifier_exists", "condition",
        "item_group_id", "google_product_category", "product_type", "color", "size", "material", "pattern",
        "gender", "age_group", "shipping_weight", "custom_label_0", "custom_label_1", "custom_label_2",
        "custom_label_3", "custom_label_4",
    ],
    default_fields=dict(_COMMON_DEFAULTS),
    required=["id", "title", "description", "link", "image_link", "availability", "price", "brand"],
    limits={
        "id": 50, "title": 150, "description": 5000, "link": 2000, "image_link": 2000,
        "product_type": 750, "custom_label": 100,
    },
    availability_labels={
        "in_stock": "in_stock", "out_of_stock": "out_of_stock", "preorder": "preorder", "backorder": "backorder",
    },
    max_additional_images=10,
)

META = ChannelSpec(
    name="meta",
    label="Meta (Facebook/Instagram) catalog",
    file_name="meta-catalog-feed.csv",
    delta_file_name="meta-catalog-feed.delta.csv",
    field_order=[
        "id", "title", "description", "availability", "condition", "price", "link", "image_link", "brand",
        "additional_image_link", "item_group_id", "sale_price", "sale_price_effective_date", "inventory",
        "color", "size", "gender", "age_group", "material", "pattern", "gtin", "mpn",
        "google_product_category", "product_type", "shipping_weight", "shipping", "custom_label_0",
        "custom_label_1", "custom_label_2", "custom_label_3", "custom_label_4",
    ],
    default_fields=dict(_COMMON_DEFAULTS, inventory="{stock_quantity}"),
    required=["id", "title", "description", "availability", "condition", "price", "link", "image_link", "brand"],
    limits={
        "id": 100, "title": 200, "description": 9999, "link": 2000, "image_link": 2000,
        "product_type": 750, "custom_label": 100,
    },
    availability_labels={
        "in_stock": "in stock", "out_of_stock": "out of stock", "preorder": "preorder",
        "backorder": "available for order",
    },
    max_additional_images=20,
)

CHANNELS: Dict[str, ChannelSpec] = {"google": GOOGLE, "meta": META}

GENDERS = {"male", "female", "unisex"}
AGE_GROUPS = {"newborn", "infant", "toddler", "kids", "adult"}
