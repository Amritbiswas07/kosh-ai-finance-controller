"""Read the three exports back in, as if they had come from real systems.

Deliberately tolerant: blank strings, '0.00' placeholders, Y/N flags, dates in
either ISO or dd-mm-yyyy, and amounts carrying currency decoration all parse.
Deliberately strict about one thing: a row that cannot be parsed is collected in
`errors` and reported, never silently dropped. A reconciliation that quietly
skips six rows will show a beautiful match rate on the rows it kept.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from .currency import CurrencyError, exponent, parse
from .money import to_paise
from .schema import (BASE_CURRENCY, BankLine, CurrencyMismatch, Dataset,
                     Invoice, PGTxn, SettlementBatch)

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y/%m/%d")
_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


class IngestError(Exception):
    pass


def _known(raw: str | None, key: str) -> str:
    """Accept any currency whose minor units are defined; refuse the rest.

    Refusing an unknown code is not squeamishness — the number of decimal
    places is what turns digits into money, and defaulting it to two is how a
    yen figure becomes a hundred times too small.
    """
    code = (raw or BASE_CURRENCY).strip().upper()
    try:
        exponent(code)
    except CurrencyError:
        raise CurrencyMismatch(key, code) from None
    return code


def _date(text: str) -> date:
    text = (text or "").strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            continue
    raise IngestError(f"unparseable date {text!r}")


def _dt(text: str) -> datetime:
    text = (text or "").strip()
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(text, f)
        except ValueError:
            continue
    raise IngestError(f"unparseable timestamp {text!r}")


def _opt(text: str) -> str | None:
    text = (text or "").strip()
    return text or None


def _yn(text: str) -> bool:
    return (text or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def load(root: Path) -> tuple[Dataset, list[str]]:
    ds, errors = Dataset(), []

    with (root / "erp_invoices.csv").open(newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            try:
                cur = _known(row.get("currency"), row.get("invoice_no", "?"))
                taxable = parse(row["taxable_amount"], cur).minor
                tax = parse(row["tax_amount"], cur).minor
                ds.invoices.append(Invoice(
                    invoice_no=row["invoice_no"].strip(), order_id=row["order_id"].strip(),
                    customer=row["customer"].strip(), invoice_date=_date(row["invoice_date"]),
                    taxable_paise=taxable, tax_paise=tax,
                    gross_paise=parse(row["gross_amount"], cur).minor,
                    currency=cur))
            except (IngestError, ValueError, KeyError) as exc:
                errors.append(f"erp_invoices.csv:{n}: {exc}")

    with (root / "pg_settlement_report.csv").open(newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            try:
                cur = _known(row.get("currency"), row.get("entity_id", "?"))
                ds.pg.append(PGTxn(
                    entity_id=row["entity_id"].strip(), type=row["type"].strip().lower(),
                    amount_paise=parse(row["amount"], cur).minor,
                    fee_paise=parse(row["fee"], cur).minor,
                    tax_paise=parse(row["tax"], cur).minor,
                    currency=cur, created_at=_dt(row["created_at"]),
                    method=row["method"].strip().lower(),
                    order_id=_opt(row.get("order_id")),
                    order_receipt=_opt(row.get("order_receipt")),
                    settlement_id=_opt(row.get("settlement_id")),
                    settlement_utr=(_opt(row.get("settlement_utr")) or "").upper() or None,
                    settled_at=_dt(row["settled_at"]) if _opt(row.get("settled_at")) else None,
                    on_hold=_yn(row.get("on_hold", "")),
                    dispute_id=_opt(row.get("dispute_id")),
                    parent_payment_id=_opt(row.get("parent_payment_id"))))
            except (IngestError, ValueError, KeyError) as exc:
                errors.append(f"pg_settlement_report.csv:{n}: {exc}")

    # A real MT940 download takes precedence over the CSV when both are present,
    # so pointing the engine at a bank's own export needs no other change.
    mt940 = next((p for p in (root / "bank_statement.sta", root / "bank_statement.mt940")
                  if p.exists()), None)
    if mt940 is not None:
        from .feeds import parse_mt940
        stmt = parse_mt940(mt940.read_text())
        ds.bank.extend(stmt.lines)
        errors.extend(f"{mt940.name}: {e}" for e in stmt.errors)
        if not stmt.balances_reconcile():
            errors.append(
                f"{mt940.name}: the statement does not agree with its own opening "
                "and closing balances — it may be truncated")
        return ds, errors

    with (root / "bank_statement.csv").open(newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            try:
                credit = to_paise(row["credit"]) if _opt(row.get("credit")) else 0
                debit = to_paise(row["debit"]) if _opt(row.get("debit")) else 0
                ds.bank.append(BankLine(
                    line_no=int(row["line_no"]), value_date=_date(row["value_date"]),
                    narration=row["narration"], ref_no=(row.get("ref_no") or "").strip(),
                    amount_paise=credit - abs(debit),
                    balance_paise=to_paise(row["balance"])))
            except (IngestError, ValueError, KeyError) as exc:
                errors.append(f"bank_statement.csv:{n}: {exc}")

    return ds, errors


def build_batches(ds: Dataset) -> list[SettlementBatch]:
    """Group gateway rows into the settlement batches the bank should have paid.

    A batch's net is the sum of member `net_paise` — gross credits, less refund
    debits, less fee, less GST on fee. This is the only number the bank can be
    expected to match, and computing it here rather than trusting a header field
    is the point: if the gateway's own total disagreed, we would want to see it.
    """
    groups: dict[str, list[PGTxn]] = {}
    for t in ds.pg:
        if t.settlement_id:
            groups.setdefault(t.settlement_id, []).append(t)

    batches = []
    for sid, members in sorted(groups.items()):
        first = members[0]
        # A batch settles in one currency. If the gateway ever mixed them the
        # net would be a meaningless sum, so the batch takes the currency of its
        # rows and `reconcile` raises the mixture separately.
        currencies = {m.currency for m in members}
        batches.append(SettlementBatch(
            settlement_id=sid,
            utr=(first.settlement_utr or "").upper(),
            settled_at=first.settled_at or first.created_at,
            members=tuple(m.entity_id for m in members),
            gross_paise=sum(m.amount_paise for m in members),
            fee_paise=sum(m.fee_paise for m in members),
            tax_paise=sum(m.tax_paise for m in members),
            net_paise=sum(m.net_paise for m in members),
            currency=first.currency if len(currencies) == 1 else "MIXED"))
    return batches
