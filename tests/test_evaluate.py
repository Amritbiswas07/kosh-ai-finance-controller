"""Scoring must be strict, and must not be reachable from the engine."""
import json
from pathlib import Path

import pytest

from kosh.evaluate import PRF, _score, evaluate
from kosh.generate import build, write
from kosh.ingest import build_batches, load
from kosh.match import reconcile


def test_prf_arithmetic():
    s = PRF(tp=8, fp=2, fn=2)
    assert s.precision == pytest.approx(0.8)
    assert s.recall == pytest.approx(0.8)
    assert s.f1 == pytest.approx(0.8)
    empty = PRF()
    assert empty.precision == empty.recall == empty.f1 == 0.0


def test_score_counts_both_sides_of_a_wrong_label():
    """A right code on the wrong record must cost a FP and a FN, not cancel out."""
    s = _score({("a", "X")}, {("b", "X")})
    assert (s.tp, s.fp, s.fn) == (0, 1, 1)


def test_engine_runs_with_the_ground_truth_deleted(tmp_path: Path):
    """The separation is behavioural, not a naming convention.

    If any part of ingest/match/position could reach the answer key, removing it
    would break the run. Removing it must change nothing.
    """
    ds, gt, inj = build(seed=77)
    write(ds, gt, inj, tmp_path, 77)
    before = reconcile(*_loaded(tmp_path))
    (tmp_path / "ground_truth.json").unlink()
    after = reconcile(*_loaded(tmp_path))
    assert {(m.leg, m.left, m.right) for m in before.matches} == \
           {(m.leg, m.left, m.right) for m in after.matches}
    assert {(f.key, f.code) for f in before.findings} == \
           {(f.key, f.code) for f in after.findings}


def _loaded(root: Path):
    ds, errors = load(root)
    assert not errors
    return ds, build_batches(ds)


def test_end_to_end_metrics(tmp_path: Path):
    ds, gt, inj = build(seed=31)
    write(ds, gt, inj, tmp_path, 31)
    ds, errors = load(tmp_path)
    assert not errors
    res = reconcile(ds, build_batches(ds))
    m = evaluate(res, ds, tmp_path / "ground_truth.json", 0.01)
    assert 0 <= m["auto_clear_rate"] <= 1
    assert m["records"] == len(ds)
    assert set(m["links"]) == {"invoice_to_payment", "settlement_to_bank",
                               "invoice_to_bank"}
    # Deterministic-only: perfect recall on classification, misses the TDS links.
    assert m["exceptions"]["overall"]["recall"] < 1.0
    assert m["links"]["invoice_to_payment"]["f1"] == 1.0
    assert m["links"]["settlement_to_bank"]["f1"] == 1.0


def test_confusion_matrix_accounts_for_clean_records(tmp_path: Path):
    ds, gt, inj = build(seed=32)
    write(ds, gt, inj, tmp_path, 32)
    ds, _ = load(tmp_path)
    res = reconcile(ds, build_batches(ds))
    m = evaluate(res, ds, tmp_path / "ground_truth.json", 0.01)
    assert "CLEAN" in json.dumps(m["confusion"])


def test_a_leg_with_nothing_to_find_is_not_scored_as_a_failure():
    """Zero support means 'no opportunity', not 'got it wrong'."""
    from kosh.evaluate import _macro
    perfect, empty = PRF(tp=5, fp=0, fn=0), PRF()
    assert empty.support == 0
    assert _macro([perfect, perfect]) == 1.0
    assert _macro([s for s in (perfect, perfect, empty) if s.support]) == 1.0
    assert _macro([]) == 0.0


def test_the_pack_records_the_seed_not_the_directory_name(tmp_path: Path):
    """Regression: meta['seed'] was Path.name, so every footer read 'seed data'."""
    import json as _json
    from kosh.generate import build as _build, write as _write
    ds, gt, inj = _build(seed=4321)
    _write(ds, gt, inj, tmp_path, 4321)
    manifest = _json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["seed"] == 4321
    assert manifest["seed"] != tmp_path.name
