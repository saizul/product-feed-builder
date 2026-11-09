"""GTIN (UPC/EAN/ISBN-13) validation."""
from __future__ import annotations

import re
from typing import Tuple


def clean_gtin(value: str) -> str:
    """Strip spaces and hyphens that merchants often type into barcode fields."""
    return re.sub(r"[\s\-]", "", str(value or ""))


def validate_gtin(value: str) -> Tuple[bool, str]:
    """Return (ok, reason). Accepts GTIN-8, 12, 13 or 14 with a valid GS1 check digit."""
    digits = clean_gtin(value)
    if not digits:
        return False, "empty GTIN"
    if not digits.isdigit():
        return False, "GTIN must contain digits only"
    if len(digits) not in (8, 12, 13, 14):
        return False, "GTIN must have 8, 12, 13 or 14 digits (got %d)" % len(digits)
    total = 0
    for i, ch in enumerate(reversed(digits[:-1])):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    expected = (10 - total % 10) % 10
    if expected != int(digits[-1]):
        return False, "invalid check digit (expected %d)" % expected
    return True, ""


def check_digit(body: str) -> int:
    """Compute the check digit for a GTIN body (the number without its last digit)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10
