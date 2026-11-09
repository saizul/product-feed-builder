"""Canonical product model shared by every source and channel."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

AVAILABILITY_VALUES = ("in_stock", "out_of_stock", "preorder", "backorder")


@dataclass
class Product:
    id: str = ""
    title: str = ""
    description: str = ""
    link: str = ""
    image_link: str = ""
    additional_images: List[str] = field(default_factory=list)
    price: Optional[Decimal] = None
    sale_price: Optional[Decimal] = None
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    currency: str = ""
    availability: str = ""
    stock_quantity: Optional[int] = None
    condition: str = ""
    brand: str = ""
    gtin: str = ""
    mpn: str = ""
    sku: str = ""
    item_group_id: str = ""
    google_product_category: str = ""
    product_type: str = ""
    categories: List[str] = field(default_factory=list)
    category_slugs: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    attributes: Dict[str, str] = field(default_factory=dict)
    weight: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @property
    def label(self) -> str:
        """Human readable identifier for error reports."""
        return self.id or str(self.extra.get("_row_label", "")) or "(no id)"


@dataclass
class FeedIssue:
    product_id: str
    channel: str
    severity: str  # "error" or "warning"
    code: str
    field: str
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "product_id": self.product_id,
            "channel": self.channel,
            "severity": self.severity,
            "code": self.code,
            "field": self.field,
            "message": self.message,
        }


class FeedBuilderError(Exception):
    """Base class for user-facing errors."""


class ConfigError(FeedBuilderError):
    pass


class SourceError(FeedBuilderError):
    pass
