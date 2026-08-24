"""Money must never be a float, and must survive real export formatting."""
import pytest
from kosh.money import fmt, pct_of, to_paise, to_rupees


@pytest.mark.parametrize("text,paise", [
    ("1,23,456.78", 12345678), ("₹ 1234.5", 123450), ("(450.00)", -45000),
    ("1234.567", 123457), ("0.1", 10), ("Rs.99", 9900), ("INR 1,000", 100000),
    ("0.005", 1), ("-12.30", -1230), (12, 1200),
])
def test_parses_real_world_amounts(text, paise):
    assert to_paise(text) == paise


def test_no_binary_float_drift():
    # The reason this module exists: 0.1 + 0.2 != 0.3 in binary floating point.
    assert to_paise("0.1") + to_paise("0.2") == to_paise("0.3")
    assert sum(to_paise("0.01") for _ in range(100)) == to_paise("1.00")


@pytest.mark.parametrize("bad", ["", "   ", "abc", "₹", "1.2.3"])
def test_rejects_rather_than_guesses(bad):
    with pytest.raises(ValueError):
        to_paise(bad)


@pytest.mark.parametrize("paise,shown", [
    (12345678, "1,23,456.78"), (100000, "1,000.00"), (999, "9.99"),
    (-45000, "-450.00"), (0, "0.00"), (100000000, "10,00,000.00"),
])
def test_indian_digit_grouping(paise, shown):
    assert fmt(paise) == shown


def test_signed_display():
    assert fmt(45000, sign=True) == "+450.00"
    assert fmt(-45000, sign=True) == "-450.00"


def test_rupees_are_exact_decimals():
    assert str(to_rupees(12345678)) == "123456.78"
    assert pct_of(25, 100) == 25
    assert pct_of(1, 0) == 0
