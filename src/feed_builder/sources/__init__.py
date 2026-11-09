"""Product sources: WooCommerce REST API, Shopify Admin API, CSV."""
from __future__ import annotations

import re
from typing import Dict, List

ATTRIBUTE_SYNONYMS = {
    "color": "color", "colour": "color", "size": "size", "material": "material", "pattern": "pattern",
    "gender": "gender", "age group": "age_group", "age_group": "age_group", "agegroup": "age_group",
}
_TAG_RE = re.compile(r"\s+")


def canonical_attribute(name: str) -> str:
    """Map store attribute names ('Colour', 'pa_size') to canonical keys; '' when not a feed attribute."""
    key = _TAG_RE.sub(" ", name.strip().lower())
    if key.startswith("pa_"):
        key = key[3:]
    return ATTRIBUTE_SYNONYMS.get(key, "")


def clean_list(values: List[str]) -> List[str]:
    return [v for v in (str(x).strip() for x in values) if v]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def split_attrs(pairs: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for name, value in pairs.items():
        key = canonical_attribute(name)
        if key and value:
            out[key] = value
    return out
