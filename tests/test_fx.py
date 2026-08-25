"""Multi-currency: revaluation, missing rates, and mixed batches."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from kosh.currency import Money, Rate, RateTable
from kosh.ingest import build_batches
from kosh.match import Disposition, reconcile
from kosh.schema import BankLine, Dataset, ExceptionCode, Invoice, PGTxn


def _usd_invoice(no="INV-USD-1", gross=118000, on=date(2026, 7, 1), cur="USD"):
    tax = round(gross * 1800 / 11800)
    return Invoice(no, f"o_{no}", "Foreign Buyer", on, gross - tax, tax, gross, cur)


def _payment(eid="pay_usd1", amount=118000, cur="USD",
             created=datetime(2026, 7, 1, 10), settled=datetime(2026, 7, 20, 11)):
    return PGTxn(eid, "payment", amount, 0, 0, created, "card",
                 order_id="o_INV-USD-1", settlement_id="setl_1",
                 settlement_utr="HDFCN260720111111", settled_at=settled,
                 currency=cur)


@pytest.fixture
def rates():
    return RateTable([
        Rate("USD", "INR", Decimal("83.10"), date(2026, 7, 1), "RBI"),
        Rate("USD", "INR", Decimal("84.40"), date(2026, 7, 15), "RBI"),
    ])


def _run(ds, rates=None):
    return reconcile(ds, build_batches(ds), rates=rates)


def test_a_rupee_only_run_raises_no_exchange_difference(corpus, rates):
    ds, _gt, _inj = corpus
    res = _run(ds, rates)
    assert not [f for f in res.findings
                if f.code is ExceptionCode.FX_REVALUATION]


def test_an_invoice_settled_at_a_different_rate_reports_the_difference(rates):
    """Both sides are correct. The gap is an exchange difference, not a break."""
    inv = _usd_invoice()
    ds = Dataset(invoices=[inv], pg=[_payment()])
    res = _run(ds, rates)
    fx = next(f for f in res.findings if f.code is ExceptionCode.FX_REVALUATION)
    # 1,180 USD booked at 83.10 = 98,058; received when the rate was 84.40 = 99,592
    assert fx.value_at_risk_paise == 9959200 - 9805800
    assert fx.disposition is Disposition.AUTO_RESOLVED     # not a break to chase
    assert "gain" in fx.proposed_action
    assert fx.evidence["booked_rate"].startswith("1 USD = 83.10 INR")
    assert fx.evidence["received_rate"].startswith("1 USD = 84.40 INR")


def test_a_weakening_currency_is_reported_as_a_loss():
    table = RateTable([
        Rate("USD", "INR", Decimal("84.40"), date(2026, 7, 1), "RBI"),
        Rate("USD", "INR", Decimal("83.10"), date(2026, 7, 15), "RBI"),
    ])
    ds = Dataset(invoices=[_usd_invoice()], pg=[_payment()])
    fx = next(f for f in _run(ds, table).findings
              if f.code is ExceptionCode.FX_REVALUATION)
    assert fx.value_at_risk_paise < 0 and "loss" in fx.proposed_action


def test_no_rate_means_no_estimate(rates):
    """A conversion without a rate is a guess. It says so instead."""
    inv = _usd_invoice(on=date(2026, 6, 1))          # before any quote
    ds = Dataset(invoices=[inv],
                 pg=[_payment(created=datetime(2026, 6, 1, 10))])
    res = _run(ds, rates)
    missing = next(f for f in res.findings
                   if f.code is ExceptionCode.FX_RATE_MISSING)
    assert missing.disposition is Disposition.NEEDS_REVIEW
    assert "USD->INR" in missing.evidence["why"]
    assert not [f for f in res.findings if f.code is ExceptionCode.FX_REVALUATION]


def test_without_a_rate_table_nothing_is_revalued(rates):
    ds = Dataset(invoices=[_usd_invoice()], pg=[_payment()])
    res = _run(ds, None)
    assert not [f for f in res.findings
                if f.code in (ExceptionCode.FX_REVALUATION,
                              ExceptionCode.FX_RATE_MISSING)]


def test_a_batch_mixing_currencies_is_refused_as_a_quantity():
    """Summing dollars and rupees produces a number that is not an amount."""
    a = _payment("pay_a", 100000, "USD")
    b = _payment("pay_b", 100000, "INR")
    ds = Dataset(invoices=[], pg=[a, b])
    batches = build_batches(ds)
    assert batches[0].currency == "MIXED"
    res = reconcile(ds, batches)
    mixed = next(f for f in res.findings
                 if f.code is ExceptionCode.MIXED_CURRENCY_BATCH)
    assert "not a quantity" in mixed.proposed_action


def test_a_single_currency_batch_carries_that_currency():
    ds = Dataset(invoices=[], pg=[_payment("pay_a", 100000, "USD"),
                                  _payment("pay_b", 200000, "USD")])
    assert build_batches(ds)[0].currency == "USD"


def test_the_exchange_difference_reaches_the_cash_bridge(rates):
    from kosh.position import bridge_rows, build_position
    ds = Dataset(invoices=[_usd_invoice()], pg=[_payment()])
    batches = build_batches(ds)
    res = reconcile(ds, batches, rates=rates)
    pos = build_position(ds, batches, res)
    assert pos.fx_revaluation == 9959200 - 9805800
    labels = [l for l, _a, _k in bridge_rows(pos)]
    assert "Exchange gain / loss on foreign invoices" in labels


def test_money_from_the_engine_still_refuses_to_mix():
    with pytest.raises(Exception):
        Money(100, "INR") + Money(100, "USD")


def test_an_export_invoice_is_not_expected_to_carry_gst():
    """Exports are zero-rated. Expecting 18% of a dollar invoice flagged every
    foreign sale as a tax break the moment multi-currency arrived."""
    zero_rated = Invoice("INV-EXP-1", "o_exp", "Foreign Buyer", date(2026, 7, 1),
                         118000, 0, 118000, "USD")
    domestic = Invoice("INV-DOM-1", "o_dom", "Local Buyer", date(2026, 7, 1),
                       100000, 0, 100000, "INR")      # missing GST, genuinely wrong
    res = reconcile(Dataset(invoices=[zero_rated, domestic], pg=[]),
                    build_batches(Dataset(invoices=[], pg=[])))
    flagged = {f.key for f in res.findings
               if f.code is ExceptionCode.TAX_LINE_MISMATCH}
    assert flagged == {"INV-DOM-1"}
