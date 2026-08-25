"""Reading bank statements in the formats banks actually send.

Until now the bank side was a CSV of my own devising, which is a weak claim:
a parser that only reads its author's format has not been tested against
reality. MT940 is the SWIFT statement format Indian banks hand out from
corporate net-banking, and it is genuinely awkward in ways a hand-rolled CSV
never is — comma decimals, two-digit years, dates split across two fields,
narrations continued over several lines, and a debit/credit marker that is a
letter rather than a column.

Supporting it means the engine can be pointed at a real download. The parser is
deliberately strict about money and forgiving about layout, and anything it
cannot read is returned as an error rather than skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .money import to_paise
from .schema import BankLine

#: :61: value date, optional entry date, D/C mark, amount, then type and refs.
_LINE_61 = re.compile(
    r"^:61:(?P<value>\d{6})(?P<entry>\d{4})?"
    r"(?P<mark>RC|RD|C|D)"
    r"(?P<funds>[A-Z])?"
    r"(?P<amount>[\d,]+)"
    r"(?P<rest>.*)$")


class FeedError(ValueError):
    pass


def _stmt_date(yymmdd: str) -> date:
    return datetime.strptime(yymmdd, "%y%m%d").date()


def _amount(raw: str) -> int:
    """MT940 writes decimals with a comma: 14605,69 is ₹14,605.69."""
    cleaned = raw.strip().replace(",", ".")
    if cleaned.count(".") > 1:                 # 1.234.567,89 style grouping
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    return to_paise(cleaned)


@dataclass
class Statement:
    account: str
    lines: list[BankLine]
    opening_paise: int | None
    closing_paise: int | None
    errors: list[str]

    def balances_reconcile(self) -> bool:
        """Does the statement agree with itself?

        A bank's own opening and closing balances bracket its movements, so a
        statement that does not add up has been truncated or edited — worth
        knowing before any of it is reconciled against anything else.
        """
        if self.opening_paise is None or self.closing_paise is None:
            return True
        moved = sum(l.amount_paise for l in self.lines)
        return self.opening_paise + moved == self.closing_paise


def parse_mt940(text: str) -> Statement:
    account, opening, closing = "", None, None
    lines: list[BankLine] = []
    errors: list[str] = []
    pending: dict | None = None
    narration: list[str] = []
    n = 0

    def flush() -> None:
        nonlocal pending, narration, n
        if pending is None:
            return
        n += 1
        text_ = " ".join(narration).strip() or pending["rest"].strip()
        lines.append(BankLine(
            line_no=n, value_date=pending["date"], narration=text_,
            ref_no=pending["ref"], amount_paise=pending["amount"],
            balance_paise=0))
        pending, narration = None, []

    for raw in text.splitlines():
        row = raw.rstrip()
        if not row:
            continue
        if row.startswith(":25:"):
            account = row[4:].strip()
        elif row.startswith((":60F:", ":60M:", ":62F:", ":62M:")):
            tag, body = row[1:4], row[5:].strip()
            try:
                mark, rest = body[0], body[1:]
                value = _amount(rest[9:])          # skip YYMMDD + 3-letter currency
                signed = value if mark.upper() == "C" else -value
                if tag.startswith("60"):
                    opening = signed
                else:
                    closing = signed
            except (ValueError, IndexError) as exc:
                errors.append(f"balance line {row[:24]!r}: {exc}")
        elif row.startswith(":61:"):
            flush()
            m = _LINE_61.match(row)
            if not m:
                errors.append(f"unparseable :61: line {row[:48]!r}")
                continue
            try:
                amount = _amount(m.group("amount"))
            except ValueError as exc:
                errors.append(f"amount in {row[:48]!r}: {exc}")
                continue
            # RC reverses a credit and RD reverses a debit, so the sign flips.
            mark = m.group("mark")
            positive = mark in ("C", "RD")
            rest = m.group("rest") or ""
            ref = ""
            if "//" in rest:
                ref = rest.split("//", 1)[1].strip()
            elif "NONREF" not in rest:
                ref = rest[4:].strip()
            try:
                pending = {"date": _stmt_date(m.group("value")),
                           "amount": amount if positive else -amount,
                           "ref": ref[:24], "rest": rest}
            except ValueError as exc:
                errors.append(f"date in {row[:48]!r}: {exc}")
                pending = None
        elif row.startswith(":86:"):
            narration.append(row[4:].strip())
        elif pending is not None and narration:
            narration.append(row.strip())          # continuation of :86:
    flush()

    # Restate the running balance from the opening figure, since MT940 does not
    # carry one per line.
    if opening is not None:
        running = opening
        restated = []
        for l in lines:
            running += l.amount_paise
            restated.append(BankLine(l.line_no, l.value_date, l.narration,
                                     l.ref_no, l.amount_paise, running))
        lines = restated

    return Statement(account, lines, opening, closing, errors)


def read_bank(path) -> Statement:
    """Read a bank statement, choosing the reader by what the file contains."""
    from pathlib import Path
    p = Path(path)
    text = p.read_text()
    if ":61:" in text or text.lstrip().startswith(":20:"):
        return parse_mt940(text)
    raise FeedError(
        f"{p.name} is not an MT940 statement. CSV statements are read by "
        "kosh.ingest.load; this reader handles the format banks export.")
