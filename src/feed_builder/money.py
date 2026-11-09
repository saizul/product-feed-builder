"""Parsing and formatting of prices."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional, Tuple

MINOR_UNITS = {
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0, "PYG": 0,
    "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3, "TND": 3,
}
_BENGALI = {ord(c): str(i) for i, c in enumerate("০১২৩৪৫৬৭৮৯")}
_CURRENCY_RE = re.compile(r"\b([A-Za-z]{3})\b")
_SYMBOLS = {"৳": "BDT", "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}


def _normalise(text: str) -> Optional[str]:
    s = text.translate(_BENGALI).replace(" ", " ").strip()
    s = re.sub(r"[^\d.,\s-]", "", s).strip()
    if not re.search(r"\d", s):
        return None
    negative = s.startswith("-")
    s = re.sub(r"\s+", "", s.lstrip("-"))
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    return ("-" if negative else "") + s


def parse_money(value: Any) -> Optional[Decimal]:
    """Parse '1,299.00', '৳ 1299', 1299, Decimal('9.5') into a Decimal; None when invalid."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = _normalise(str(value))
    if s is None:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def split_money(value: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    """Return (amount, currency) from text like '1299.00 BDT' or '$12.50'."""
    text = str(value or "")
    currency = None
    m = _CURRENCY_RE.search(text)
    if m:
        currency = m.group(1).upper()
    else:
        for sym, code in _SYMBOLS.items():
            if sym in text:
                currency = code
                break
    return parse_money(text), currency


def format_money(amount: Decimal, currency: str) -> str:
    """Format as '1299.00 BDT' using the currency's minor-unit precision."""
    places = MINOR_UNITS.get(currency.upper(), 2)
    quant = Decimal(1).scaleb(-places)
    return "%s %s" % (amount.quantize(quant, rounding=ROUND_HALF_UP), currency.upper())
