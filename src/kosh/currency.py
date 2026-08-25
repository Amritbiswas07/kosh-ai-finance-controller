"""Money that knows what it is, and rates that say when they were true.

Kosh began single-currency, and the shortcut showed: amounts were bare integers
of paise, so nothing stopped ₹100 being added to $100. Refusing every non-INR
row at the door was the honest stopgap, but it is not an answer for a merchant
whose international payments settle after conversion.

Three things this module insists on, each because the alternative is a wrong
number nobody notices:

**Minor units are per currency.** Rupees, dollars and euros have two decimal
places. Yen has none; Kuwaiti dinars have three. Treating ¥1,000 as 1,000 minor
units when it is 1,000 whole yen is an error of a hundredfold, and it looks
exactly like a correct figure.

**Different currencies never add.** `Money` refuses it rather than coercing,
because a total that silently mixes currencies is worse than a crash: it
reconciles.

**A conversion is an event, not a function call.** It happened on a date, at a
rate, from a source, and all three are recorded on the result. "What rate did
you use?" is the first question an auditor asks about a revalued figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Currency code -> number of decimal places, per ISO 4217. The zero- and
#: three-decimal entries are the reason this table exists at all.
MINOR_UNITS: dict[str, int] = {
    "INR": 2, "USD": 2, "EUR": 2, "GBP": 2, "AED": 2, "SGD": 2,
    "AUD": 2, "CAD": 2, "CHF": 2, "HKD": 2, "MYR": 2, "SAR": 2,
    "JPY": 0, "KRW": 0, "VND": 0,
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3,
}

#: What the books are kept in. Everything reports here in the end.
BASE = "INR"


class CurrencyError(ValueError):
    pass


class RateMissing(CurrencyError):
    def __init__(self, src: str, dst: str, on: date) -> None:
        super().__init__(
            f"no {src}->{dst} rate on or before {on}. A conversion without a "
            "rate is a guess, so the amount is left unconverted and reported "
            "rather than estimated.")
        self.src, self.dst, self.on = src, dst, on


def exponent(code: str) -> int:
    try:
        return MINOR_UNITS[code.upper()]
    except KeyError:
        raise CurrencyError(
            f"unknown currency {code!r}. Add it to MINOR_UNITS with its number "
            "of decimal places; guessing two is how JPY ends up wrong by 100."
        ) from None


@dataclass(frozen=True, order=False)
class Money:
    """An integer number of minor units, and what they are minor units of."""

    minor: int
    currency: str = BASE

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", self.currency.upper())
        exponent(self.currency)                 # rejects unknown codes early

    # ------------------------------------------------------------ arithmetic
    def _same(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyError(
                f"cannot combine {self.currency} and {other.currency} directly. "
                "Convert one through a dated rate first — a total that mixes "
                "currencies reconciles, which is what makes it dangerous.")

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    def __abs__(self) -> "Money":
        return Money(abs(self.minor), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._same(other)
        return self.minor < other.minor

    def __bool__(self) -> bool:
        return self.minor != 0

    # ------------------------------------------------------------- rendering
    @property
    def units(self) -> Decimal:
        e = exponent(self.currency)
        return (Decimal(self.minor) / (Decimal(10) ** e)).quantize(
            Decimal(1).scaleb(-e))

    def format(self, *, sign: bool = False, code: bool = True) -> str:
        """Indian grouping for rupees, thousands elsewhere."""
        e = exponent(self.currency)
        neg = self.minor < 0
        whole, frac = divmod(abs(self.minor), 10 ** e) if e else (abs(self.minor), 0)
        digits = str(whole)
        if self.currency == "INR" and len(digits) > 3:
            head, tail = digits[:-3], digits[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            digits = ",".join(groups + [tail])
        elif len(digits) > 3:
            digits = f"{whole:,}"
        body = digits if e == 0 else f"{digits}.{frac:0{e}d}"
        out = ("-" if neg else "+" if sign else "") + body
        return f"{out} {self.currency}" if code else out

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return f"Money({self.minor}, {self.currency!r})"


def parse(value: str | int | Decimal, currency: str = BASE) -> Money:
    """Parse a written amount into minor units of `currency`."""
    e = exponent(currency)
    if isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, Decimal):
        dec = value
    else:
        text = str(value).strip()
        if not text:
            raise CurrencyError("empty amount")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        for junk in ("₹", "$", "€", "£", "¥", "INR", "USD", "EUR", "GBP", "JPY",
                     "Rs.", "Rs", ",", " "):
            text = text.replace(junk, "")
        if not text:
            raise CurrencyError(f"{value!r} is only currency decoration")
        try:
            dec = Decimal(text)
        except InvalidOperation:
            raise CurrencyError(f"not an amount: {value!r}") from None
        if negative:
            dec = -dec
    scaled = (dec * (Decimal(10) ** e)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Money(int(scaled), currency)


# --------------------------------------------------------------------- rates

@dataclass(frozen=True)
class Rate:
    """One currency's price in another, on a stated day, from a stated source."""

    src: str
    dst: str
    rate: Decimal
    as_of: date
    source: str = "manual"

    def __post_init__(self) -> None:
        object.__setattr__(self, "src", self.src.upper())
        object.__setattr__(self, "dst", self.dst.upper())
        if self.rate <= 0:
            raise CurrencyError(f"a rate must be positive, got {self.rate}")

    def describe(self) -> str:
        return f"1 {self.src} = {self.rate} {self.dst} ({self.as_of}, {self.source})"


@dataclass(frozen=True)
class Converted:
    """The result of a conversion, carrying the rate that produced it."""

    amount: Money
    original: Money
    rate: Rate

    def evidence(self) -> dict:
        return {"original": self.original.format(),
                "converted": self.amount.format(),
                "rate": str(self.rate.rate),
                "rate_date": self.rate.as_of.isoformat(),
                "rate_source": self.rate.source}


class RateTable:
    """Dated rates, looked up as of a day.

    A settlement converted today is not converted at today's rate if it moved
    last Tuesday, so lookup takes the most recent rate **on or before** the
    date asked for and never a later one. Missing is an error, not a fallback:
    silently reusing a stale rate is how a revaluation drifts.
    """

    def __init__(self, rates: "list[Rate] | None" = None) -> None:
        self._by_pair: dict[tuple[str, str], list[Rate]] = {}
        for r in rates or []:
            self.add(r)

    def add(self, rate: Rate) -> None:
        bucket = self._by_pair.setdefault((rate.src, rate.dst), [])
        bucket.append(rate)
        bucket.sort(key=lambda r: r.as_of)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_pair.values())

    def pairs(self) -> list[tuple[str, str]]:
        return sorted(self._by_pair)

    def lookup(self, src: str, dst: str, on: date) -> Rate:
        src, dst = src.upper(), dst.upper()
        if src == dst:
            return Rate(src, dst, Decimal(1), on, "identity")
        for rate in reversed(self._by_pair.get((src, dst), [])):
            if rate.as_of <= on:
                return rate
        # An inverse quote is a legitimate way to hold the pair, but it is
        # derived, and the result says so rather than pretending to be quoted.
        for rate in reversed(self._by_pair.get((dst, src), [])):
            if rate.as_of <= on:
                return Rate(src, dst, Decimal(1) / rate.rate, rate.as_of,
                            f"{rate.source} (inverted)")
        raise RateMissing(src, dst, on)

    def convert(self, money: Money, to: str, on: date) -> Converted:
        rate = self.lookup(money.currency, to, on)
        target_e = exponent(to)
        source_e = exponent(money.currency)
        # Work in whole units so a conversion between currencies with different
        # numbers of decimals cannot be scaled by the wrong power of ten.
        whole = Decimal(money.minor) / (Decimal(10) ** source_e)
        converted = (whole * rate.rate * (Decimal(10) ** target_e)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP)
        return Converted(Money(int(converted), to), money, rate)


# ----------------------------------------------------------------- rate files

def load_rates(path) -> RateTable:
    """Read a rate file: src,dst,rate,as_of,source.

    Deliberately a file rather than a live feed. A reconciliation has to be
    reproducible months later, and a figure that moves because someone re-ran it
    on a different day is not evidence. The rates used are an input to the run,
    versioned alongside the statements they applied to.
    """
    import csv
    from pathlib import Path as _P

    p = _P(path)
    if not p.exists():
        return RateTable()
    table, errors = RateTable(), []
    with p.open(newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            try:
                table.add(Rate(
                    src=row["src"], dst=row["dst"], rate=Decimal(row["rate"].strip()),
                    as_of=date.fromisoformat(row["as_of"].strip()),
                    source=(row.get("source") or "file").strip()))
            except (KeyError, ValueError, InvalidOperation, CurrencyError) as exc:
                errors.append(f"{p.name}:{n}: {exc}")
    if errors:
        raise CurrencyError("; ".join(errors))
    return table


def write_rates(table: RateTable, path) -> None:
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src", "dst", "rate", "as_of", "source"])
        for pair in table.pairs():
            for r in table._by_pair[pair]:
                w.writerow([r.src, r.dst, r.rate, r.as_of.isoformat(), r.source])
