from datetime import datetime, timezone
from decimal import Decimal

import pytest

from feed_builder.config import parse_config
from feed_builder.models import Product

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def cfg():
    return parse_config({"currency": "BDT", "store_url": "https://shop.example.com", "timezone": "+06:00"})


def make_product(**over):
    base = dict(
        id="SKU-1", title="Linen Shirt", description="A breathable linen shirt for warm days.",
        link="https://shop.example.com/product/linen-shirt/", image_link="https://cdn.example.com/shirt.jpg",
        price=Decimal("1299"), availability="in_stock", brand="Example Wear", gtin="4006381333931",
        categories=["Shirts"], category_slugs=["shirts"], source="test",
    )
    base.update(over)
    return Product(**base)


@pytest.fixture
def product():
    return make_product
