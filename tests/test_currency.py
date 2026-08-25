"""Money that knows what it is, and rates that say when they were true."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from kosh.currency import (BASE, MINOR_UNITS, CurrencyError, Money, Rate,
                           RateMissing, RateTable, exponent, parse)


# ------------------------------------------------------------- minor units

@pytest.mark.parametrize("code,places", [("INR", 2), ("USD", 2), ("JPY", 0),
                                         ("KRW", 0), ("KWD", 3), ("BHD", 3)])
def test_each_currency_knows_its_own_decimal_places(code, places):
    assert exponent(code) == places


def test_yen_is_not_two_decimals():
    """Treating 1,000 yen as 10.00 is wrong by a hundredfold and looks correct."""
    yen = parse("1000", "JPY")
    assert yen.minor == 1000
    assert yen.units == Decimal("1000")
    assert yen.format() == "1,000 JPY"
    # The same written figure in rupees is a hundred times more minor units.
    assert parse("1000", "INR").minor == 100000


def test_three_decimal_currencies_keep_all_three():
    kwd = parse("12.345", "KWD")
    assert kwd.minor == 12345 and str(kwd.units) == "12.345"


def test_an_unknown_currency_is_refused_not_assumed():
    with pytest.raises(CurrencyError, match="unknown currency"):
        Money(100, "XYZ")
    with pytest.raises(CurrencyError, match="guessing two"):
        exponent("ZZZ")


# ---------------------------------------------------------------- arithmetic

def test_different_currencies_never_add():
    """A total that silently mixes currencies reconciles. That is what makes it
    dangerous."""
    with pytest.raises(CurrencyError, match="cannot combine"):
        parse("100", "INR") + parse("100", "USD")
    with pytest.raises(CurrencyError):
        parse("100", "INR") - parse("100", "USD")
    with pytest.raises(CurrencyError):
        parse("100", "INR") < parse("100", "USD")


def test_same_currency_arithmetic_is_exact():
    a, b = parse("0.10", "INR"), parse("0.20", "INR")
    assert (a + b) == parse("0.30", "INR")
    assert sum((parse("0.01", "INR") for _ in range(100)),
               Money(0, "INR")) == parse("1.00", "INR")
    assert (-a).minor == -10 and abs(-a) == a


def test_currency_is_normalised_and_case_insensitive():
    assert Money(1, "inr").currency == "INR"
    assert parse("1.00", "usd") == parse("1.00", "USD")


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("text,minor", [
    ("1,23,456.78", 12345678), ("₹ 1234.5", 123450), ("(450.00)", -45000),
    ("1234.567", 123457), ("-12.30", -1230),
])
def test_rupee_shapes_parse(text, minor):
    assert parse(text, "INR").minor == minor


def test_dollar_and_euro_decoration_is_stripped():
    assert parse("$1,500.00", "USD").minor == 150000
    assert parse("€99.99", "EUR").minor == 9999


@pytest.mark.parametrize("bad", ["", "   ", "abc", "₹", "1.2.3"])
def test_junk_is_refused(bad):
    with pytest.raises(CurrencyError):
        parse(bad, "INR")


def test_rupee_grouping_is_indian_and_others_are_not():
    assert parse("12345678", "INR").format(code=False) == "1,23,45,678.00"
    assert parse("12345678", "USD").format(code=False) == "12,345,678.00"


# -------------------------------------------------------------------- rates

@pytest.fixture
def table():
    return RateTable([
        Rate("USD", "INR", Decimal("83.10"), date(2026, 7, 1), "RBI"),
        Rate("USD", "INR", Decimal("83.60"), date(2026, 7, 10), "RBI"),
        Rate("EUR", "INR", Decimal("90.20"), date(2026, 7, 1), "RBI"),
    ])


def test_a_rate_must_be_positive():
    with pytest.raises(CurrencyError, match="positive"):
        Rate("USD", "INR", Decimal("0"), date(2026, 7, 1))


def test_lookup_takes_the_rate_in_force_not_the_latest_one(table):
    """A settlement that moved last Tuesday is not converted at today's rate."""
    assert table.lookup("USD", "INR", date(2026, 7, 5)).rate == Decimal("83.10")
    assert table.lookup("USD", "INR", date(2026, 7, 10)).rate == Decimal("83.60")
    assert table.lookup("USD", "INR", date(2026, 7, 20)).rate == Decimal("83.60")


def test_a_missing_rate_is_an_error_not_a_stale_fallback(table):
    with pytest.raises(RateMissing, match="no USD->INR rate on or before"):
        table.lookup("USD", "INR", date(2026, 6, 30))     # before any quote
    with pytest.raises(RateMissing):
        table.lookup("GBP", "INR", date(2026, 7, 5))      # pair not held


def test_the_same_currency_needs_no_rate(table):
    r = table.lookup("INR", "INR", date(2026, 7, 5))
    assert r.rate == 1 and r.source == "identity"


def test_an_inverted_quote_says_that_it_is_derived(table):
    r = table.lookup("INR", "USD", date(2026, 7, 5))
    assert "inverted" in r.source
    assert abs(r.rate - (Decimal(1) / Decimal("83.10"))) < Decimal("1e-20")


# --------------------------------------------------------------- conversion

def test_conversion_records_the_rate_that_produced_it(table):
    got = table.convert(parse("1500.00", "USD"), "INR", date(2026, 7, 5))
    assert got.amount == parse("124650.00", "INR")       # 1500 * 83.10
    assert got.rate.as_of == date(2026, 7, 1) and got.rate.source == "RBI"
    ev = got.evidence()
    assert ev["rate"] == "83.10" and ev["original"] == "1,500.00 USD"


def test_conversion_between_different_decimal_places_is_not_scaled_wrongly():
    """USD has two decimals and JPY none. Getting this wrong is a 100x error."""
    t = RateTable([Rate("USD", "JPY", Decimal("157"), date(2026, 7, 1), "test")])
    got = t.convert(parse("10.00", "USD"), "JPY", date(2026, 7, 1))
    assert got.amount == Money(1570, "JPY")              # ¥1,570, not ¥157,000
    back = RateTable([Rate("JPY", "USD", Decimal("0.00637"), date(2026, 7, 1), "t")])
    assert back.convert(Money(1570, "JPY"), "USD", date(2026, 7, 1)).amount.minor == 1000


def test_conversion_into_a_three_decimal_currency():
    t = RateTable([Rate("INR", "KWD", Decimal("0.00366"), date(2026, 7, 1), "t")])
    got = t.convert(parse("100000.00", "INR"), "KWD", date(2026, 7, 1))
    assert got.amount == parse("366.000", "KWD")


def test_converting_to_the_same_currency_changes_nothing(table):
    m = parse("500.00", "INR")
    got = table.convert(m, "INR", date(2026, 7, 5))
    assert got.amount == m and got.rate.source == "identity"


def test_rounding_is_half_up_at_the_minor_unit():
    t = RateTable([Rate("USD", "INR", Decimal("83.335"), date(2026, 7, 1), "t")])
    # 1.00 USD * 83.335 = 83.335 INR -> 83.34
    assert t.convert(parse("1.00", "USD"), "INR", date(2026, 7, 1)).amount \
        == parse("83.34", "INR")


def test_the_base_currency_is_what_the_books_are_kept_in():
    assert BASE in MINOR_UNITS and Money(1).currency == BASE
