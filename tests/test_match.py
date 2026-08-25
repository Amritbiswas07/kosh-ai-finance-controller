"""The matching cascade: each tier, and the guards that keep it honest."""
import random

import pytest

from kosh.ingest import build_batches
from kosh.match import (ADJUDICATION_TOL, AMOUNT_TOL, Disposition, Leg, Tier,
                        _tds_relation, expected_fee, reconcile)
from kosh.schema import MDR_BPS, ExceptionCode


def test_every_deterministic_tier_is_exercised(run):
    _ds, _gt, _b, res = run
    fired = {m.tier for m in res.matches}
    for tier in (Tier.EXACT_ID, Tier.NORMALIZED_ID, Tier.AMOUNT_DATE, Tier.AGGREGATE):
        assert tier in fired, f"{tier} never fired — it is untested on this corpus"


def test_no_model_means_no_adjudicated_matches(run):
    _ds, _gt, _b, res = run
    assert not [m for m in res.matches if m.tier is Tier.ADJUDICATED]


def test_assignment_is_stable_under_row_shuffling(corpus):
    """Greedy matching would depend on input order; the Hungarian pass must not."""
    ds, _gt, _inj = corpus
    baseline = {(m.leg, m.left, m.right) for m in reconcile(ds, build_batches(ds)).matches}
    for seed in (1, 2, 3):
        shuffled = type(ds)(invoices=list(ds.invoices), pg=list(ds.pg), bank=list(ds.bank))
        rng = random.Random(seed)
        rng.shuffle(shuffled.invoices)
        rng.shuffle(shuffled.pg)
        again = reconcile(shuffled, build_batches(shuffled))
        assert {(m.leg, m.left, m.right) for m in again.matches} == baseline


def test_a_record_is_never_matched_twice(run):
    _ds, _gt, _b, res = run
    for leg in (Leg.ERP_PG, Leg.BATCH_BANK, Leg.INVOICE_BANK):
        claimed = []
        for m in res.matches:
            if m.leg is leg:
                claimed.append(m.left)
                claimed.extend(m.right)
        assert len(claimed) == len(set(claimed)), f"{leg} double-claimed a record"


def test_findings_are_never_double_counted(run):
    _ds, _gt, _b, res = run
    seen = {(f.key, f.code) for f in res.findings}
    assert len(seen) == len(res.findings)


def test_split_settlement_sums_exactly_and_needs_no_human(run):
    _ds, _gt, _b, res = run
    splits = [f for f in res.findings if f.code is ExceptionCode.SPLIT_SETTLEMENT]
    assert splits
    for f in splits:
        assert f.disposition is Disposition.AUTO_RESOLVED
        assert f.value_at_risk_paise == 0
        assert len(f.evidence["lines"]) > 1


def test_merged_payout_names_the_batches_it_was_paid_with(run):
    _ds, _gt, _b, res = run
    merged = [f for f in res.findings if f.code is ExceptionCode.MERGED_PAYOUT]
    assert merged
    for f in merged:
        assert f.disposition is Disposition.AUTO_RESOLVED
        assert f.evidence["paid_with"]
        assert f.evidence["utr_absent_from_narration"] is True


def test_subset_sum_refuses_an_ambiguous_tie():
    """Two equally good combinations must yield no match rather than a guess."""
    from kosh.match import _match_merged_payouts, ReconResult
    from kosh.schema import SettlementBatch, BankLine
    from datetime import date, datetime

    def batch(i, net):
        return SettlementBatch(f"setl_{i}", f"UTR{i}", datetime(2026, 7, 10),
                               (f"pay_{i}",), net, 0, 0, net)
    # 100+300 and 200+200 both make 400.
    batches = {b.settlement_id: b for b in
               (batch(1, 10000), batch(2, 20000), batch(3, 30000), batch(4, 20000))}
    line = BankLine(1, date(2026, 7, 10), "NEFT RAZORPAY CONSOLIDATED PAYOUT",
                    "1", 40000, 0)
    lines = {line.key: line}
    res = ReconResult()
    _match_merged_payouts(batches, lines, res)
    assert not res.matches
    assert len(batches) == 4 and len(lines) == 1


def test_fee_variance_is_measured_against_the_contracted_mdr(run):
    ds, _gt, _b, res = run
    by_id = {t.entity_id: t for t in ds.pg}
    for f in (x for x in res.findings if x.code is ExceptionCode.FEE_VARIANCE):
        t = by_id[f.key]
        fee, tax = expected_fee(t.amount_paise, t.method)
        assert (t.fee_paise + t.tax_paise) - (fee + tax) == f.value_at_risk_paise
        assert f.evidence["contracted_bps"] == MDR_BPS[t.method]


def test_upi_is_zero_mdr():
    assert expected_fee(1_000_00, "upi") == (0, 0)


@pytest.mark.parametrize("gross,credit,rel", [
    (100000, 100000, "gross"),
    (100000, 98000, "net_of_tds_2pct"),
    (100000, 99000, "net_of_tds_1pct"),
    (100000, 90000, "net_of_tds_10pct"),
    (100000, 95000, None),          # 5% is not a rate we recognise
    (100000, 50000, None),
])
def test_tds_relation_only_accepts_statutory_rates(gross, credit, rel):
    assert _tds_relation(gross, credit) == rel


def test_tds_relation_respects_the_amount_tolerance():
    assert _tds_relation(100000, 100000 + AMOUNT_TOL) == "gross"
    assert _tds_relation(100000, 100000 + AMOUNT_TOL + 1) is None


def test_unresolved_carries_exposure_and_an_action(run):
    _ds, _gt, _b, res = run
    unresolved = res.unresolved()
    assert unresolved
    for f in unresolved:
        assert f.proposed_action, f"{f.key} has no proposed action"
        assert f.evidence, f"{f.key} has no evidence"


def test_auto_resolved_items_are_genuinely_settled(run):
    _ds, _gt, _b, res = run
    for f in res.findings:
        if f.disposition is Disposition.AUTO_RESOLVED:
            assert f.code in {ExceptionCode.SPLIT_SETTLEMENT, ExceptionCode.MERGED_PAYOUT,
                              ExceptionCode.FEE_VARIANCE, ExceptionCode.TDS_WITHHELD,
                              ExceptionCode.PART_PAYMENT}


def test_timings_cover_all_four_legs(run):
    _ds, _gt, _b, res = run
    for key in ("erp_to_gateway_s", "integrity_s", "settlement_to_bank_s",
                "direct_receipts_s", "total_s"):
        assert key in res.timings


def test_over_collected_gst_asks_for_a_credit_note_not_an_invoice(run):
    """Both directions of a tax break need the right remedy, and no negative amounts."""
    _ds, _gt, _b, res = run
    tax = [f for f in res.findings if f.code is ExceptionCode.TAX_LINE_MISMATCH]
    assert tax
    assert any(f.value_at_risk_paise < 0 for f in tax), "no over-collection in this corpus"
    import re
    for f in tax:
        # No negative rupee amount should ever reach the controller's action text.
        assert not re.search(r"-\d+\.\d{2}", f.proposed_action), f.proposed_action
        if f.value_at_risk_paise < 0:
            assert "credit note" in f.proposed_action
        else:
            assert "revised invoice" in f.proposed_action


# ---------------------------------------------------------------- P0 regressions

def _mini(invoices, pg, bank=()):
    from kosh.schema import Dataset
    return Dataset(invoices=list(invoices), pg=list(pg), bank=list(bank))


def _inv(no, order, taxable, cust="Acme"):
    from datetime import date
    from kosh.schema import Invoice
    tax = round(taxable * 1800 / 10_000)
    return Invoice(no, order, cust, date(2026, 7, 1), taxable, tax, taxable + tax)


def _pay(eid, order, amt, hour=10, day=1, receipt=None):
    from datetime import datetime
    from kosh.schema import PGTxn
    return PGTxn(eid, "payment", amt, 0, 0, datetime(2026, 7, day, hour), "upi",
                 order_id=order, order_receipt=receipt)


def test_an_identifier_match_with_a_money_gap_is_still_an_exception():
    """The worst bug this engine had: a 10,000 invoice paid with 100 shared an
    order_id, so it was reported as a clean match and never reached the
    exception list. The match rate counted a 9,900 hole as a win."""
    inv = _inv("INV-1", "o1", 8_474_58)
    ds = _mini([inv], [_pay("pay_1", "o1", 1_00_00)])
    res = reconcile(ds, build_batches(ds))
    assert [m.left for m in res.matches] == ["INV-1"]        # the link is right
    short = [f for f in res.findings if f.code is ExceptionCode.SHORT_PAYMENT]
    assert len(short) == 1
    assert short[0].value_at_risk_paise == 1_00_00 - inv.gross_paise
    assert short[0].disposition is Disposition.NEEDS_REVIEW


def test_an_overpayment_is_named_as_one():
    inv = _inv("INV-1", "o1", 8_474_58)
    ds = _mini([inv], [_pay("pay_1", "o1", inv.gross_paise + 5_000_00)])
    res = reconcile(ds, build_batches(ds))
    codes = {f.code for f in res.findings}
    assert ExceptionCode.OVERPAYMENT in codes
    assert ExceptionCode.SHORT_PAYMENT not in codes


def test_a_gap_matching_a_statutory_tds_rate_is_not_called_a_short_payment():
    inv = _inv("INV-1", "o1", 8_474_58)
    net = inv.gross_paise - round(inv.gross_paise * 200 / 10_000)      # 2% u/s 194C
    ds = _mini([inv], [_pay("pay_1", "o1", net)])
    res = reconcile(ds, build_batches(ds))
    codes = {f.code for f in res.findings}
    assert ExceptionCode.TDS_WITHHELD in codes
    assert ExceptionCode.SHORT_PAYMENT not in codes


def test_instalments_are_not_duplicates_and_are_never_told_to_refund():
    """Calling a part payment a duplicate was not just a wrong label — the
    proposed action said 'refund it', which would take back money owed."""
    inv = _inv("INV-2", "o2", 8_474_58)
    half = inv.gross_paise // 2
    ds = _mini([inv], [_pay("pay_a", "o2", half),
                       _pay("pay_b", "o2", inv.gross_paise - half, day=3)])
    res = reconcile(ds, build_batches(ds))
    codes = {f.code for f in res.findings}
    assert ExceptionCode.PART_PAYMENT in codes
    assert ExceptionCode.DUPLICATE_PAYMENT not in codes
    part = next(f for f in res.findings if f.code is ExceptionCode.PART_PAYMENT)
    assert "refund" not in part.proposed_action.lower()
    # Both captures are linked to the one invoice.
    agg = next(m for m in res.matches if m.tier is Tier.AGGREGATE)
    assert set(agg.right) == {"pay_a", "pay_b"}


def test_duplicates_are_caught_when_the_export_carries_no_order_id():
    inv = _inv("INV-3", "o3", 4_237_29)
    ds = _mini([inv], [_pay("pay_a", None, inv.gross_paise, receipt="INV-3"),
                       _pay("pay_b", None, inv.gross_paise, hour=18, receipt="INV-3")])
    res = reconcile(ds, build_batches(ds))
    dupes = [f for f in res.findings if f.code is ExceptionCode.DUPLICATE_PAYMENT]
    assert [f.key for f in dupes] == ["pay_b"]               # the later capture


def test_a_gap_too_large_for_a_bank_charge_is_admitted_as_unclassified():
    """UNCLASSIFIED existed to stop the engine naming a cause it does not know,
    and had never once fired: every settlement delta became
    SETTLEMENT_AMOUNT_MISMATCH with a confident note about a bank charge."""
    from datetime import date, datetime
    from kosh.schema import BankLine, PGTxn
    inv = _inv("INV-FX", "oFX", 1_00_000_00)
    pg = PGTxn("pay_fx", "payment", inv.gross_paise, 0, 0, datetime(2026, 7, 1, 10),
               "card", order_id="oFX", settlement_id="setl_fx",
               settlement_utr="HDFCN260703111111", settled_at=datetime(2026, 7, 3, 11))
    line = BankLine(1, date(2026, 7, 3), "NEFT-HDFCN260703111111-RAZORPAY-SETTLEMENT",
                    "111111", inv.gross_paise - 4_200_00, 0)
    ds = _mini([inv], [pg], [line])
    res = reconcile(ds, build_batches(ds))
    codes = {f.code for f in res.findings}
    assert ExceptionCode.UNCLASSIFIED in codes
    assert ExceptionCode.SETTLEMENT_AMOUNT_MISMATCH not in codes


def test_a_charge_sized_gap_is_still_named_as_a_charge():
    from datetime import date, datetime
    from kosh.schema import BankLine, PGTxn
    inv = _inv("INV-C", "oC", 1_00_000_00)
    pg = PGTxn("pay_c", "payment", inv.gross_paise, 0, 0, datetime(2026, 7, 1, 10),
               "card", order_id="oC", settlement_id="setl_c",
               settlement_utr="HDFCN260703222222", settled_at=datetime(2026, 7, 3, 11))
    line = BankLine(1, date(2026, 7, 3), "NEFT-HDFCN260703222222-RAZORPAY-SETTLEMENT",
                    "222222", inv.gross_paise - 23_60, 0)      # ₹23.60, a real charge
    ds = _mini([inv], [pg], [line])
    res = reconcile(ds, build_batches(ds))
    codes = {f.code for f in res.findings}
    assert ExceptionCode.SETTLEMENT_AMOUNT_MISMATCH in codes
    assert ExceptionCode.UNCLASSIFIED not in codes
