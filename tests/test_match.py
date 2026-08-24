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
                              ExceptionCode.FEE_VARIANCE, ExceptionCode.TDS_WITHHELD}


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
