"""Parsing must be tolerant of format, and loud about failure."""
import csv
from pathlib import Path

from kosh.generate import build, write
from kosh.ingest import build_batches, load


def _corpus(tmp_path: Path) -> Path:
    ds, gt, inj = build(seed=7)
    write(ds, gt, inj, tmp_path, 7)
    return tmp_path


def test_round_trips_without_loss(tmp_path):
    original, gt, inj = build(seed=7)
    write(original, gt, inj, tmp_path, 7)
    loaded, errors = load(tmp_path)
    assert not errors
    assert loaded.counts() == original.counts()
    assert (sum(t.amount_paise for t in loaded.pg)
            == sum(t.amount_paise for t in original.pg))


def test_bad_rows_are_reported_not_dropped(tmp_path):
    root = _corpus(tmp_path)
    path = root / "bank_statement.csv"
    rows = list(csv.reader(path.open()))
    rows.append(["999", "not-a-date", "GARBAGE", "x", "", "abc", "0"])
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    ds, errors = load(root)
    # The failure is surfaced with a line number, and the good rows still load.
    assert len(errors) == 1 and "bank_statement.csv:" in errors[0]
    assert len(ds.bank) >= 1


def test_batch_net_is_recomputed_from_members(tmp_path):
    ds, _ = load(_corpus(tmp_path))
    batches = build_batches(ds)
    assert batches
    by_id = {}
    for t in ds.pg:
        if t.settlement_id:
            by_id.setdefault(t.settlement_id, []).append(t)
    for b in batches:
        members = by_id[b.settlement_id]
        assert b.net_paise == sum(m.net_paise for m in members)
        assert len(b.members) == len(members)
        assert b.utr


def test_on_hold_rows_never_join_a_batch(tmp_path):
    ds, _ = load(_corpus(tmp_path))
    assert any(t.on_hold for t in ds.pg)
    assert all(t.settlement_id is None for t in ds.pg if t.on_hold)


def test_debits_and_credits_collapse_to_one_signed_amount(tmp_path):
    ds, _ = load(_corpus(tmp_path))
    assert any(l.amount_paise > 0 for l in ds.bank)
    assert any(l.amount_paise < 0 for l in ds.bank)
    assert all(l.amount_paise != 0 for l in ds.bank)


def test_a_foreign_currency_invoice_is_refused(tmp_path):
    """Invoice.currency was parsed and then never looked at by anything."""
    root = _corpus(tmp_path)
    path = root / "erp_invoices.csv"
    rows = list(csv.reader(path.open()))
    rows.append(["INV-USD-1", "o_usd", "Foreign Buyer", "2026-07-04",
                 "1000.00", "180.00", "1180.00", "USD"])
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    ds, errors = load(root)
    assert len(errors) == 1 and "USD" in errors[0]
    assert all(i.invoice_no != "INV-USD-1" for i in ds.invoices)


def test_a_foreign_currency_gateway_row_is_refused(tmp_path):
    root = _corpus(tmp_path)
    path = root / "pg_settlement_report.csv"
    rows = list(csv.reader(path.open()))
    rows.append(["pay_usd1", "payment", "0.00", "1500.00", "1500.00", "USD",
                 "30.00", "5.40", "N", "N", "2026-07-04 10:00:00", "", "", "",
                 "o_usd", "INV-USD-1", "card", "", ""])
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    ds, errors = load(root)
    assert len(errors) == 1 and "USD" in errors[0]
    assert all(t.entity_id != "pay_usd1" for t in ds.pg)
