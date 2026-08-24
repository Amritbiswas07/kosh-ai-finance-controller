"""Money is integer paise. Nothing in Kosh ever holds a rupee amount in a float.

A reconciliation engine that uses binary floating point will, on a large enough
run, report a mismatch that does not exist: 0.1 + 0.2 != 0.3 is a rounding
artefact, but a controller reading the exception report cannot tell it apart
from a real 30-paise shortfall. So amounts are `int` paise end to end, parsed
from text with `Decimal`, and only converted to a string at the display edge.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE = Decimal(100)


def to_paise(value: str | int | float | Decimal) -> int:
    """Parse a rupee amount into integer paise.

    Accepts the shapes that turn up in real bank exports: '1,23,456.78',
    '₹ 1234.5', '(450.00)' for a debit, '1234.567' (rounded half-up).
    """
    if isinstance(value, int):
        return value * 100
    if isinstance(value, Decimal):
        dec = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty amount")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        for junk in ("₹", "INR", "Rs.", "Rs", ",", " "):
            text = text.replace(junk, "")
        if not text:
            raise ValueError("amount was only currency decoration")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not an amount: {value!r}") from exc
        if negative:
            dec = -dec
    return int((dec * PAISE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_rupees(paise: int) -> Decimal:
    """Exact rupee Decimal. For display and for report JSON."""
    return (Decimal(paise) / PAISE).quantize(Decimal("0.01"))


def fmt(paise: int, *, sign: bool = False) -> str:
    """Indian-grouped display string, e.g. -12,34,567.89."""
    negative = paise < 0
    whole, frac = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    body = f"{digits}.{frac:02d}"
    if negative:
        return f"-{body}"
    return f"+{body}" if sign else body


def pct_of(part: int, whole: int) -> Decimal:
    """Basis-point-accurate percentage, guarding division by zero."""
    if whole == 0:
        return Decimal(0)
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.01"))
