"""MT940: the format banks actually export."""
from __future__ import annotations

import pytest

from kosh.feeds import FeedError, parse_mt940, read_bank

SAMPLE = """:20:STARTUMS
:25:HDFC/00600350012345
:28C:00001/001
:60F:C260701INR2450000,00
:61:2607030703C14605,69NTRFNONREF//03589071
:86:ACH/C/SBIN0260703589071/RAZORPAY SOFTWARE
:61:2607040704D8936,54NTRFNONREF//40751440
:86:RENT-UNIT 402 SKYVIEW-MAR
:61:2607050705C22641,85NTRFNONREF//04675657
:86:MB:NEFT CR-ICICN260704675657-RAZORPAY-
SETTLEMENT
:62F:C260705INR2478311,00
"""


@pytest.fixture(scope="module")
def stmt():
    return parse_mt940(SAMPLE)


def test_it_reads_the_account_and_every_movement(stmt):
    assert stmt.account == "HDFC/00600350012345"
    assert len(stmt.lines) == 3 and not stmt.errors


def test_comma_decimals_become_exact_paise(stmt):
    """MT940 writes 14605,69 where a CSV would write 14605.69."""
    assert stmt.lines[0].amount_paise == 1460569
    assert stmt.lines[2].amount_paise == 2264185


def test_the_debit_credit_mark_carries_the_sign(stmt):
    """There is no debit column; direction is a letter in the middle of a field."""
    assert stmt.lines[0].amount_paise > 0        # C
    assert stmt.lines[1].amount_paise < 0        # D
    assert stmt.lines[1].amount_paise == -893654


def test_a_narration_continued_on_the_next_line_is_kept_whole(stmt):
    assert "ICICN260704675657" in stmt.lines[2].narration
    assert "SETTLEMENT" in stmt.lines[2].narration


def test_the_running_balance_is_restated_from_the_opening_figure(stmt):
    """MT940 gives no per-line balance, only the brackets."""
    assert stmt.opening_paise == 245000000
    running = stmt.opening_paise
    for line in stmt.lines:
        running += line.amount_paise
        assert line.balance_paise == running
    assert stmt.lines[-1].balance_paise == stmt.closing_paise


def test_a_statement_that_disagrees_with_itself_is_caught():
    """Opening plus movements must equal closing, or the file is truncated."""
    assert parse_mt940(SAMPLE).balances_reconcile()
    truncated = "\n".join(l for l in SAMPLE.splitlines()
                          if "8936,54" not in l and "SKYVIEW" not in l)
    assert not parse_mt940(truncated).balances_reconcile()


def test_reversals_flip_the_sign():
    rc = parse_mt940(":60F:C260701INR0,00\n:61:2607030703RC500,00NTRFNONREF//1\n:86:X\n")
    rd = parse_mt940(":60F:C260701INR0,00\n:61:2607030703RD500,00NTRFNONREF//1\n:86:X\n")
    assert rc.lines[0].amount_paise == -50000     # a reversed credit is a debit
    assert rd.lines[0].amount_paise == 50000


def test_an_unreadable_movement_is_reported_not_skipped():
    bad = ":60F:C260701INR0,00\n:61:GARBAGE\n:86:X\n:61:2607030703C1,00NTRFNONREF//1\n:86:Y\n"
    st = parse_mt940(bad)
    assert len(st.errors) == 1 and "unparseable" in st.errors[0]
    assert len(st.lines) == 1                     # the good one still loads


def test_reading_a_file_that_is_not_mt940_says_so(tmp_path):
    p = tmp_path / "bank_statement.sta"
    p.write_text("line_no,value_date\n1,2026-07-01\n")
    with pytest.raises(FeedError, match="not an MT940"):
        read_bank(p)


def test_an_mt940_download_is_used_in_place_of_the_csv(tmp_path):
    """Point the engine at a bank's own export and nothing else changes."""
    from kosh.generate import build, write
    from kosh.ingest import load

    ds, gt, inj = build(seed=9)
    write(ds, gt, inj, tmp_path, 9)
    (tmp_path / "bank_statement.sta").write_text(SAMPLE)

    loaded, errors = load(tmp_path)
    assert not errors
    assert len(loaded.bank) == 3                  # the MT940, not the 60-odd CSV rows
    assert loaded.bank[0].narration.startswith("ACH/C/SBIN")
    assert loaded.invoices and loaded.pg          # the other two sources still load
