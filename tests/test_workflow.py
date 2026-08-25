"""Exception lifecycle: assignment, maker-checker, and learning from a person."""
from __future__ import annotations

import pytest

from kosh.ingest import build_batches
from kosh.match import Tier, reconcile
from kosh.store import APPROVAL_THRESHOLD_PAISE, Store


@pytest.fixture
def live(tmp_path, corpus):
    ds, _gt, _inj = corpus
    s = Store(tmp_path / "k.db")
    rep = s.sync(ds, reconcile(ds, build_batches(ds)))
    yield s, ds, rep
    s.close()


def _pick(store, below=None, at_least=None):
    sql = ("SELECT key, code FROM exception WHERE status='open'"
           + (" AND ABS(value_at_risk) < ?" if below else "")
           + (" AND ABS(value_at_risk) >= ?" if at_least else "")
           + " ORDER BY ABS(value_at_risk) DESC LIMIT 1")
    row = store.db.execute(sql, tuple(x for x in (below, at_least) if x)).fetchone()
    assert row, "no suitable exception"
    return row[0], row[1]


def test_assigning_moves_it_out_of_the_unowned_pile(live):
    store, _ds, _rep = live
    key, code = _pick(store)
    store.assign(key, code, "priya", by="amrit")
    row = store._row(key, code)
    assert row["assignee"] == "priya" and row["status"] == "investigating"
    assert store.counts()["assigned"] == 1
    # Still open work, not closed work.
    assert (key, code) in store.open_exceptions()


def test_a_small_break_closes_on_one_signature(live):
    store, _ds, _rep = live
    key, code = _pick(store, below=APPROVAL_THRESHOLD_PAISE)
    store.resolve(key, code, by="priya", note="bank confirmed the charge")
    row = store._row(key, code)
    assert row["status"] == "resolved" and row["resolved_by"] == "priya"
    assert row["approved_by"] is None


def test_a_large_break_cannot_be_closed_alone(live):
    """A control one person can satisfy alone is not a control."""
    store, _ds, _rep = live
    key, code = _pick(store, at_least=APPROVAL_THRESHOLD_PAISE)
    with pytest.raises(PermissionError, match="second approver"):
        store.resolve(key, code, by="priya", note="written off")
    with pytest.raises(PermissionError, match="cannot approve their own"):
        store.resolve(key, code, by="priya", note="written off", approved_by="priya")
    with pytest.raises(PermissionError, match="cannot approve their own"):
        store.resolve(key, code, by="Priya", note="x", approved_by=" priya ")
    store.resolve(key, code, by="priya", note="unrecoverable",
                  status="written_off", approved_by="amrit")
    row = store._row(key, code)
    assert row["status"] == "written_off" and row["approved_by"] == "amrit"


def test_a_decision_survives_the_next_run(live):
    """The run must not reopen what a person deliberately closed."""
    store, ds, _rep = live
    key, code = _pick(store, below=APPROVAL_THRESHOLD_PAISE)
    store.resolve(key, code, by="priya", note="agreed with the customer")
    again = store.sync(ds, reconcile(ds, build_batches(ds)))
    assert (key, code) not in {(k, c) for k, c, _v in again.opened}
    assert (key, code) not in store.open_exceptions()
    assert store._row(key, code)["status"] == "resolved"


def test_a_confirmed_link_is_replayed_and_never_asked_again(live):
    """The feedback loop: once a controller says these two are the same event,
    the engine stops raising it."""
    store, ds, rep = live
    missing = next(k for k, c, _v in rep.opened if c == "MISSING_IN_BANK")
    credit = next(k for k, c, _v in rep.opened if c == "UNEXPECTED_BANK_CREDIT")
    store.confirm_link("settlement_to_bank", missing, credit, by="amrit",
                       note="gateway confirmed by email")

    confirmed = store.manual_links()
    assert ("settlement_to_bank", missing, credit) in confirmed

    res = reconcile(ds, build_batches(ds), confirmed=confirmed)
    top = [m for m in res.matches if m.tier is Tier.CONFIRMED]
    assert [(m.left, m.right[0]) for m in top] == [(missing, credit)]
    # Neither side is raised as a break any more.
    still = {f.key for f in res.findings}
    assert missing not in still and credit not in still

    after = store.sync(ds, res)
    assert {missing, credit} <= {k for k, _c, _v, _h in after.resolved}


def test_a_confirmation_for_another_period_is_ignored(live):
    """Replaying a link whose records are not in this snapshot must not invent
    a match for records that are not there."""
    store, ds, _rep = live
    store.confirm_link("settlement_to_bank", "setl_not_here", "bank:9999",
                       by="amrit")
    res = reconcile(ds, build_batches(ds), confirmed=store.manual_links())
    assert not [m for m in res.matches if m.tier is Tier.CONFIRMED]


def test_every_action_is_attributable(live):
    store, _ds, _rep = live
    key, code = _pick(store, below=APPROVAL_THRESHOLD_PAISE)
    store.assign(key, code, "priya", by="amrit")
    store.annotate(key, code, "chasing the bank", by="priya")
    store.resolve(key, code, by="priya", note="done")
    actions = [(a, actor) for _at, actor, a, _s, _d in store.history()]
    assert ("assign", "amrit") in actions
    assert ("note", "priya") in actions
    assert ("resolved", "priya") in actions


def test_an_unknown_exception_is_refused_not_created(live):
    store, _ds, _rep = live
    with pytest.raises(KeyError):
        store.assign("nope", "NOPE", "priya", by="amrit")
    with pytest.raises(ValueError):
        key, code = _pick(store, below=APPROVAL_THRESHOLD_PAISE)
        store.resolve(key, code, by="p", note="x", status="banished")
