"""Product filtering rules (exclude out-of-stock, categories, price bounds, custom conditions)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .models import Product
from .money import parse_money
from .rules import evaluate


def _norm(values: List[Any]) -> set:
    return {str(v).strip().lower() for v in values}


def filter_reason(p: Product, filters: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[str]:
    """Return why a product is excluded, or None to keep it."""
    if filters.get("exclude_out_of_stock") and p.availability == "out_of_stock":
        return "out of stock"
    cats = _norm(p.categories + p.category_slugs)
    if filters.get("exclude_categories") and cats & _norm(filters["exclude_categories"]):
        return "excluded category"
    if filters.get("include_categories") and not cats & _norm(filters["include_categories"]):
        return "not in an included category"
    if filters.get("exclude_ids") and _norm([p.id, p.sku]) & _norm(filters["exclude_ids"]):
        return "excluded id"
    lo = parse_money(filters.get("min_price")) if filters.get("min_price") is not None else None
    hi = parse_money(filters.get("max_price")) if filters.get("max_price") is not None else None
    if p.price is not None:
        if lo is not None and p.price < Decimal(lo):
            return "below min_price"
        if hi is not None and p.price > Decimal(hi):
            return "above max_price"
    for cond in filters.get("exclude_if", []) or []:
        if evaluate(cond, ctx):
            return "matched exclude_if"
    return None


def apply_filters(
    items: List[Tuple[Product, Dict[str, Any]]], filters: Dict[str, Any]
) -> Tuple[List[Tuple[Product, Dict[str, Any]]], List[Tuple[str, str]]]:
    if not filters:
        return items, []
    kept, dropped = [], []
    for p, ctx in items:
        reason = filter_reason(p, filters, ctx)
        if reason:
            dropped.append((p.label, reason))
        else:
            kept.append((p, ctx))
    return kept, dropped
