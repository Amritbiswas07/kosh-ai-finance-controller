"""Properties that must hold for any input, not for the cases I thought of.

Every other test here checks a situation I imagined. That is exactly the
critique worth taking seriously: a suite written by the same person as the
engine tests what that person expected. These assert *invariants* over
hundreds of randomly built and randomly damaged corpora, so the shapes are not
ones anybody chose.

The invariants are the ones whose violation means money is wrong:

  conservation      no record disappears silently
  disclosure        a match that hides a gap must say so
  the bridge        gross entering settlement always equals the batch nets
  exclusivity       no record is claimed twice
  independence      the answer does not depend on row order
  determinism       the same input gives the same answer
  groundedness      every match and finding names a record that exists

The ₹9,900 hole that shipped for weeks was a disclosure failure: a match that
concealed a difference. It would have been caught here on the first run.
"""
from __future__ import annotations

import random

import pytest

from kosh.generate import Injections, build
from kosh.ingest import build_batches
from kosh.match import AMOUNT_TOL, Disposition, Leg, Tier, reconcile
from kosh.position import build_position
from kosh.schema import Dataset

#: Enough shapes to be interesting, few enough to stay a fast test.
CORPORA = 60


def damaged(rng: random.Random, ds: Dataset) -> Dataset:
    """Break the corpus the way real exports are broken.

    Rows go missing, get duplicated, lose identifiers, or arrive out of order.
    None of this is in the generator's design; that is the point.
    """
    inv, pg, bank = list(ds.invoices), list(ds.pg), list(ds.bank)

    def drop(rows, share):
        n = int(len(rows) * share)
        return [r for i, r in enumerate(rows) if i >= n or rng.random() > 0.5]

    if rng.random() < 0.4:
        inv = drop(inv, rng.uniform(0.02, 0.15))
    if rng.random() < 0.4:
        pg = drop(pg, rng.uniform(0.02, 0.15))
    if rng.random() < 0.4:
        bank = drop(bank, rng.uniform(0.02, 0.15))
    if rng.random() < 0.3 and pg:                      # a re-sent export
        pg = pg + [pg[rng.randrange(len(pg))]]
    if rng.random() < 0.3 and inv:                     # blanked references
        i = rng.randrange(len(inv))
        inv[i] = type(inv[i])(**{**inv[i].__dict__, "order_id": ""})
    rng.shuffle(inv)
    rng.shuffle(pg)
    rng.shuffle(bank)
    return Dataset(invoices=inv, pg=pg, bank=bank)


def corpus(seed: int) -> Dataset:
    rng = random.Random(seed)
    inj = Injections.jittered(rng, spread=0.9, scale=rng.uniform(0.3, 3.0))
    ds, _gt, _inj = build(seed=seed, n_orders=rng.randrange(30, 160), inj=inj)
    return damaged(rng, ds) if rng.random() < 0.6 else ds


@pytest.fixture(scope="module")
def runs():
    """One reconciliation per random corpus, reused by every invariant."""
    out = []
    for seed in range(1, CORPORA + 1):
        ds = corpus(seed)
        batches = build_batches(ds)
        out.append((seed, ds, batches, reconcile(ds, batches)))
    return out


def _keys(ds: Dataset, batches) -> set[str]:
    return ({i.invoice_no for i in ds.invoices} | {t.entity_id for t in ds.pg}
            | {b.key for b in ds.bank} | {b.settlement_id for b in batches})


# ---------------------------------------------------------------- groundedness

def test_every_match_names_records_that_exist(runs):
    for seed, ds, batches, res in runs:
        known = _keys(ds, batches)
        for m in res.matches:
            assert m.left in known, f"seed {seed}: match on unknown {m.left}"
            for r in m.right:
                assert r in known, f"seed {seed}: match on unknown {r}"


def test_every_finding_names_a_record_that_exists(runs):
    for seed, ds, batches, res in runs:
        known = _keys(ds, batches)
        for f in res.findings:
            assert f.key in known, f"seed {seed}: finding on unknown {f.key}"


# ----------------------------------------------------------------- exclusivity

def test_no_record_is_claimed_by_two_matches_on_a_leg(runs):
    for seed, _ds, _b, res in runs:
        for leg in Leg:
            claimed = []
            for m in res.matches:
                if m.leg is leg:
                    claimed.append(m.left)
                    claimed.extend(m.right)
            dupes = {k for k in claimed if claimed.count(k) > 1}
            assert not dupes, f"seed {seed}: {leg.value} claimed {dupes} twice"


def test_a_finding_is_never_raised_twice_for_the_same_reason(runs):
    for seed, _ds, _b, res in runs:
        pairs = [(f.key, f.code) for f in res.findings]
        assert len(pairs) == len(set(pairs)), f"seed {seed}: duplicated finding"


# ------------------------------------------------------------------ disclosure

def test_a_match_that_hides_a_money_gap_always_says_so(runs):
    """The ₹9,900 hole: an order_id agreed, the amounts did not, and the match
    was reported clean. Any match carrying a difference must be accompanied by a
    finding on one of its records."""
    for seed, _ds, _b, res in runs:
        flagged = {f.key for f in res.findings}
        for m in res.matches:
            if abs(m.delta_paise) <= AMOUNT_TOL or m.tier is Tier.CONFIRMED:
                continue
            touched = {m.left, *m.right}
            assert touched & flagged, (
                f"seed {seed}: {m.left} -> {m.right} carries "
                f"{m.delta_paise}p with nothing raised")


def test_nothing_needing_review_is_reported_as_zero_exposure(runs):
    for seed, _ds, _b, res in runs:
        for f in res.findings:
            if f.disposition is Disposition.NEEDS_REVIEW:
                assert f.proposed_action, f"seed {seed}: {f.key} has no action"


# ----------------------------------------------------------------- conservation

def test_no_source_record_disappears_without_a_word(runs):
    """Every record is matched, carries a finding, or belongs to a batch that
    matched. A record that is none of those has been silently dropped, and a
    reconciliation that quietly loses rows shows a fine match rate on the ones
    it kept."""
    for seed, ds, batches, res in runs:
        accounted = {f.key for f in res.findings}
        for m in res.matches:
            accounted.add(m.left)
            accounted.update(m.right)
        by_id = {b.settlement_id: b for b in batches}
        for sid in list(accounted):
            if sid in by_id:
                accounted.update(by_id[sid].members)

        missing = []
        for t in ds.pg:
            if t.entity_id in accounted:
                continue
            if t.settlement_id in accounted:
                continue
            missing.append(t.entity_id)
        for i in ds.invoices:
            if i.invoice_no not in accounted:
                missing.append(i.invoice_no)
        assert not missing, f"seed {seed}: {len(missing)} record(s) vanished: {missing[:4]}"


# ---------------------------------------------------------------- the bridge

def test_the_cash_bridge_always_balances(runs):
    for seed, ds, batches, res in runs:
        pos = build_position(ds, batches, res)
        assert pos.residual == 0, f"seed {seed}: residual {pos.residual}p"
        assert pos.landed_in_bank + pos.in_transit == pos.batch_net
        assert (pos.on_hold_gross + pos.unbatched_gross
                + pos.gross_entering_settlement) == pos.captured


# ------------------------------------------------------- order and determinism

def test_the_answer_does_not_depend_on_row_order():
    """Greedy matching would make the result depend on how the file was sorted.
    Reordering every source must change nothing."""
    for seed in range(1, 21):
        ds = corpus(seed)
        base = reconcile(ds, build_batches(ds))
        rng = random.Random(seed * 31)
        shuffled = Dataset(invoices=list(ds.invoices), pg=list(ds.pg),
                           bank=list(ds.bank))
        rng.shuffle(shuffled.invoices)
        rng.shuffle(shuffled.pg)
        rng.shuffle(shuffled.bank)
        other = reconcile(shuffled, build_batches(shuffled))
        assert ({(m.leg, m.left, tuple(sorted(m.right))) for m in base.matches}
                == {(m.leg, m.left, tuple(sorted(m.right))) for m in other.matches}), \
            f"seed {seed}: matching depends on row order"
        assert ({(f.key, f.code) for f in base.findings}
                == {(f.key, f.code) for f in other.findings}), \
            f"seed {seed}: findings depend on row order"


def test_the_same_input_gives_the_same_answer():
    for seed in range(1, 16):
        ds = corpus(seed)
        a = reconcile(ds, build_batches(ds))
        b = reconcile(ds, build_batches(ds))
        assert [m.to_json() for m in a.matches] == [m.to_json() for m in b.matches]
        assert [f.to_json() for f in a.findings] == [f.to_json() for f in b.findings]


def test_an_empty_world_reconciles_to_nothing():
    ds = Dataset()
    res = reconcile(ds, build_batches(ds))
    assert res.matches == [] and res.findings == []
    assert build_position(ds, [], res).residual == 0
