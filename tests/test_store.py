"""State between runs: idempotency, late arrivals, and exception ageing."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from kosh.generate import build
from kosh.ingest import build_batches
from kosh.match import reconcile
from kosh.schema import Dataset
from kosh.store import Store, _fingerprint


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "k.db")
    yield s
    s.close()


def _sync(store, ds):
    return store.sync(ds, reconcile(ds, build_batches(ds)))


def _split(ds: Dataset, hold: int):
    """A day-1 view with the most recent credits not yet arrived."""
    credits = [b for b in sorted(ds.bank, key=lambda b: b.value_date) if b.is_credit]
    held = {b.key for b in credits[-hold:]}
    early = Dataset(invoices=list(ds.invoices), pg=list(ds.pg),
                    bank=[b for b in ds.bank if b.key not in held])
    return early, held


def test_loading_the_same_export_twice_changes_nothing(store, corpus):
    ds, _gt, _inj = corpus
    first = _sync(store, ds)
    assert first.new_records == len(ds)
    second = _sync(store, ds)
    assert second.new_records == 0
    assert second.new_links == 0
    assert second.opened == [] and second.resolved == []
    # And the ledger did not grow.
    assert store.counts()["records"] == len(ds)


def test_an_amended_row_counts_as_new_work(store, corpus):
    ds, _gt, _inj = corpus
    _sync(store, ds)
    amended = dataclasses.replace(ds.invoices[0],
                                  taxable_paise=ds.invoices[0].taxable_paise + 1_00)
    changed = Dataset(invoices=[amended] + list(ds.invoices[1:]),
                      pg=list(ds.pg), bank=list(ds.bank))
    rep = _sync(store, changed)
    assert rep.new_records == 1


def test_a_late_credit_clears_yesterdays_break(store, corpus):
    """The engine is unchanged between the two runs. Only the data caught up."""
    ds, _gt, _inj = corpus
    early, held = _split(ds, 4)

    day1 = _sync(store, early)
    opened = {k for k, c, _ in day1.opened if c == "MISSING_IN_BANK"}
    assert opened, "no settlement was left in flight to test with"

    day2 = _sync(store, ds)
    assert day2.new_records == len(held)
    cleared = {k for k, c, _v, _h in day2.resolved}
    assert opened & cleared, "a break that the new credits answer stayed open"
    for _k, _c, _v, how in day2.resolved:
        assert how in {"matched once the data arrived", "condition no longer present"}


def test_a_resolved_break_stays_resolved(store, corpus):
    ds, _gt, _inj = corpus
    early, _ = _split(ds, 4)
    _sync(store, early)
    _sync(store, ds)
    resolved_after_two = store.counts()["resolved_exceptions"]
    third = _sync(store, ds)
    assert third.resolved == []
    assert store.counts()["resolved_exceptions"] == resolved_after_two


def test_an_unanswered_break_visibly_ages(store, corpus):
    ds, _gt, _inj = corpus
    _sync(store, ds)
    _sync(store, ds)
    third = _sync(store, ds)
    assert third.carried, "nothing was carried forward"
    assert max(age for *_, age in third.carried) == 2


def test_links_are_recorded_once_not_once_per_run(store, corpus):
    ds, _gt, _inj = corpus
    first = _sync(store, ds)
    assert first.new_links > 0
    before = store.counts()["links"]
    _sync(store, ds)
    assert store.counts()["links"] == before


def test_fingerprints_track_content_not_identity(corpus):
    ds, _gt, _inj = corpus
    a = ds.invoices[0]
    assert _fingerprint(a) == _fingerprint(dataclasses.replace(a))
    assert _fingerprint(a) != _fingerprint(
        dataclasses.replace(a, taxable_paise=a.taxable_paise + 1))


def test_the_engine_never_reads_the_store():
    """Matching stays free of hidden state — it is the audited part."""
    import inspect
    from kosh import ingest, match, position
    for mod in (match, ingest, position):
        assert "store" not in inspect.getsource(mod).lower().replace(
            "restore", ""), mod.__name__
