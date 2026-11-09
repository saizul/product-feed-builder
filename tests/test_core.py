from datetime import timezone
from decimal import Decimal

import pytest

from feed_builder.dates import format_interval, parse_dt, parse_interval, parse_tz
from feed_builder.gtin import check_digit, clean_gtin, validate_gtin
from feed_builder.money import format_money, parse_money, split_money


# ------------------------------------------------------------------ GTIN
@pytest.mark.parametrize("code", ["4006381333931", "036000291452", "96385074", "10614141000415", "9780306406157"])
def test_gtin_valid(code):
    assert validate_gtin(code) == (True, "")


@pytest.mark.parametrize("code", ["4006381333932", "036000291453", "96385075"])
def test_gtin_bad_check_digit(code):
    ok, why = validate_gtin(code)
    assert not ok and "check digit" in why


@pytest.mark.parametrize("code", ["123", "12345678901", "123456789012345"])
def test_gtin_bad_length(code):
    ok, why = validate_gtin(code)
    assert not ok and "digits" in why


def test_gtin_non_digit_and_empty():
    assert not validate_gtin("ABC12345678")[0]
    assert validate_gtin("")[1] == "empty GTIN"


def test_gtin_cleaning_spaces_and_hyphens():
    assert clean_gtin("4006 3813-33931") == "4006381333931"
    assert validate_gtin("4006-3813-33931")[0]


def test_check_digit_function():
    assert check_digit("400638133393") == 1


# ------------------------------------------------------------------ money
@pytest.mark.parametrize("text,expected", [
    ("1,299.00", "1299.00"), ("1.299,00", "1299.00"), ("12,50", "12.50"), ("৳ 1299", "1299"),
    ("১২৩৪", "1234"), ("৳৪,৫০০", "4500"), ("$ 9.99", "9.99"), ("1 299,50", "1299.50"), ("0", "0"),
])
def test_parse_money(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("bad", [None, "", "free", "abc", True])
def test_parse_money_invalid(bad):
    assert parse_money(bad) is None


def test_parse_money_numbers():
    assert parse_money(10) == Decimal("10")
    assert parse_money(9.5) == Decimal("9.5")
    assert parse_money(Decimal("1.25")) == Decimal("1.25")
    assert parse_money(Decimal("NaN")) is None


@pytest.mark.parametrize("amount,cur,expected", [
    ("1299", "BDT", "1299.00 BDT"), ("9.5", "usd", "9.50 USD"), ("1299.999", "BDT", "1300.00 BDT"),
    ("1500", "JPY", "1500 JPY"), ("2.5", "KWD", "2.500 KWD"), ("0.005", "USD", "0.01 USD"),
])
def test_format_money(amount, cur, expected):
    assert format_money(Decimal(amount), cur) == expected


def test_split_money():
    assert split_money("1299.00 BDT") == (Decimal("1299.00"), "BDT")
    assert split_money("৳500") == (Decimal("500"), "BDT")
    assert split_money("$12.50") == (Decimal("12.50"), "USD")
    assert split_money("1,000") == (Decimal("1000"), None)


# ------------------------------------------------------------------ dates
def test_parse_tz():
    assert parse_tz("+06:00").utcoffset(None).total_seconds() == 6 * 3600
    assert parse_tz("-0530").utcoffset(None).total_seconds() == -(5 * 3600 + 1800)
    assert parse_tz("UTC") is timezone.utc
    with pytest.raises(ValueError):
        parse_tz("Asia/Dhaka")


@pytest.mark.parametrize("text", [
    "2026-03-01T00:00:00Z", "2026-03-01T06:00:00+06:00", "2026-03-01T06:00:00+0600", "2026-03-01 00:00:00",
    "2026-03-01T00:00",
])
def test_parse_dt_variants(text):
    dt = parse_dt(text)
    assert dt is not None and dt.utcoffset() is not None


def test_parse_dt_naive_uses_default_tz():
    dt = parse_dt("2026-03-01T00:00:00", parse_tz("+06:00"))
    assert dt.utcoffset().total_seconds() == 6 * 3600


def test_parse_dt_date_only_start_and_end_of_day():
    assert parse_dt("2026-03-31").hour == 0
    end = parse_dt("2026-03-31", end_of_day=True)
    assert (end.hour, end.minute) == (23, 59)


@pytest.mark.parametrize("bad", ["", "not a date", "2026-13-45"])
def test_parse_dt_invalid(bad):
    assert parse_dt(bad) is None


def test_format_interval_in_target_timezone():
    start, end = parse_dt("2026-03-01T00:00:00Z"), parse_dt("2026-03-31T17:59:00Z")
    assert format_interval(start, end, parse_tz("+06:00")) == "2026-03-01T06:00+06:00/2026-03-31T23:59+06:00"


def test_format_interval_negative_offset():
    start, end = parse_dt("2026-03-01T00:00:00Z"), parse_dt("2026-03-02T00:00:00Z")
    assert format_interval(start, end, parse_tz("-05:30")).startswith("2026-02-28T18:30-05:30/")


def test_parse_interval_roundtrip():
    s, e = parse_interval("2026-03-01T00:00+06:00/2026-03-31T23:59+06:00")
    assert format_interval(s, e, parse_tz("+06:00")) == "2026-03-01T00:00+06:00/2026-03-31T23:59+06:00"
    assert parse_interval("garbage") == (None, None)
