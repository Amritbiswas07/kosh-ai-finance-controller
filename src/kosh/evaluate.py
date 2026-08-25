"""Scoring the run against ground truth.

This is the only module that opens `ground_truth.json`. Two things are measured,
and they are different questions:

  *Linking*  — did the engine connect the records that genuinely belong
               together? Scored as precision/recall/F1 over unordered pairs,
               per leg.
  *Classification* — for everything that did not link, did the engine give it
               the right reason? Scored strictly, over (record, code) pairs, so
               putting the right label on the wrong row earns a false positive
               and a false negative rather than cancelling out.

The headline `auto_clear_rate` is deliberately conservative: a record counts as
cleared only if it was correctly linked *and* carries no finding that needs a
human. Records the engine explained but could not resolve are not counted as
wins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .match import Disposition, Leg, ReconResult
from .money import to_rupees
from .schema import Dataset


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_json(self) -> dict:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn,
                "precision": round(self.precision, 4), "recall": round(self.recall, 4),
                "f1": round(self.f1, 4), "support": self.support}


def _links(mapping: dict) -> set[tuple[str, str]]:
    """Ground-truth links, tolerant of both shapes it has had.

    An invoice used to map to a single payment id; it now maps to a list,
    because an invoice can be settled by several captures. Iterating the older
    string form yields one pair per *character* — 1,035 of them from 135
    invoices — and the evaluator happily scored that as F1 0.0 rather than
    saying the file was stale. Normalising here is the fix; the silence was the
    bug.
    """
    out: set[tuple[str, str]] = set()
    for key, value in mapping.items():
        if isinstance(value, str):
            out.add((key, value))
        else:
            out.update((key, v) for v in value)
    return out


def _macro(scores: list[PRF]) -> float:
    return sum(s.f1 for s in scores) / len(scores) if scores else 0.0


def _score(predicted: set, truth: set) -> PRF:
    return PRF(tp=len(predicted & truth), fp=len(predicted - truth), fn=len(truth - predicted))


def _pairs_erp(res: ReconResult) -> set[tuple[str, str]]:
    """Every (invoice, payment) link, including instalments where one invoice is
    settled by several captures. Reading only `right[0]` silently dropped the
    second half of every part payment."""
    return {(m.left, key) for m in res.matches
            if m.leg is Leg.ERP_PG for key in m.right}


def _pairs_direct(res: ReconResult) -> set[tuple[str, str]]:
    return {(m.left, m.right[0]) for m in res.matches
            if m.leg is Leg.INVOICE_BANK and len(m.right) == 1}


def _pairs_bank(res: ReconResult) -> set[tuple[str, str]]:
    """Normalise both match directions into (settlement_id, bank_line_key)."""
    out = set()
    for m in res.matches:
        if m.leg is not Leg.BATCH_BANK:
            continue
        if m.left.startswith("bank:"):            # subset-sum: N batches → 1 credit
            out.update((sid, m.left) for sid in m.right)
        else:                                     # 1 batch → N credits
            out.update((m.left, key) for key in m.right)
    return out


def evaluate(res: ReconResult, ds: Dataset, gt_path: Path, wall_seconds: float) -> dict:
    gt = json.loads(gt_path.read_text())

    link_scores = {
        "invoice_to_payment": _score(
            _pairs_erp(res), _links(gt["invoice_to_payment"])),
        "settlement_to_bank": _score(
            _pairs_bank(res),
            {(sid, key) for sid, keys in gt["batch_to_bank"].items() for key in keys}),
        "invoice_to_bank": _score(
            _pairs_direct(res), {(k, v) for k, v in gt.get("invoice_to_bank", {}).items()}),
    }

    # --- exception classification, strict over (record, code) ----------------
    pred_pairs = {(f.key, f.code.value) for f in res.findings}
    true_pairs = {(k, v) for k, v in gt["exceptions"].items()}
    overall = _score(pred_pairs, true_pairs)

    codes = sorted({c for _, c in pred_pairs | true_pairs})
    per_code: dict[str, PRF] = {}
    for code in codes:
        per_code[code] = _score({p for p in pred_pairs if p[1] == code},
                                {p for p in true_pairs if p[1] == code})

    # A readable confusion matrix needs one label per record, so collapse
    # multi-finding records to the finding with the largest exposure.
    primary: dict[str, str] = {}
    for f in sorted(res.findings, key=lambda f: (-abs(f.value_at_risk_paise), f.code.value)):
        primary.setdefault(f.key, f.code.value)
    truth_of = dict(gt["exceptions"])
    confusion: dict[str, dict[str, int]] = {}
    for key in set(primary) | set(truth_of):
        t = truth_of.get(key, "CLEAN")
        p = primary.get(key, "CLEAN")
        confusion.setdefault(t, {}).setdefault(p, 0)
        confusion[t][p] += 1

    # --- headline throughput and clear rate ----------------------------------
    total_records = len(ds)
    needs_review_keys = {f.key for f in res.findings
                         if f.disposition is Disposition.NEEDS_REVIEW}
    correctly_linked: set[str] = set()
    for a, b in _pairs_erp(res) & _links(gt["invoice_to_payment"]):
        correctly_linked.update((a, b))
    for sid, key in _pairs_bank(res) & {(s, k) for s, ks in gt["batch_to_bank"].items()
                                        for k in ks}:
        correctly_linked.add(key)
    for a, b in _pairs_direct(res) & {(k, v) for k, v in gt.get("invoice_to_bank", {}).items()}:
        correctly_linked.update((a, b))
    # Gateway rows inside a correctly linked batch are cleared with it.
    linked_batches = {sid for sid, _ in _pairs_bank(res)}
    for t in ds.pg:
        if t.settlement_id in linked_batches and t.entity_id not in needs_review_keys:
            correctly_linked.add(t.entity_id)
    cleared = correctly_linked - needs_review_keys

    unresolved = res.unresolved()
    exposure = sum(abs(f.value_at_risk_paise) for f in unresolved)

    macro_f1 = sum(s.f1 for s in per_code.values()) / len(per_code) if per_code else 0.0

    return {
        "records": total_records,
        "wall_seconds": round(wall_seconds, 4),
        "records_per_second": round(total_records / wall_seconds) if wall_seconds else None,
        "engine_seconds": res.timings.get("total_s"),
        "auto_clear_rate": round(len(cleared) / total_records, 4) if total_records else 0.0,
        "auto_cleared_records": len(cleared),
        "links": {k: v.to_json() for k, v in link_scores.items()},
        # Macro over legs that had something to find. A leg with zero true links
        # scores F1 0.0 by definition, and averaging that in reports a collapse
        # that never happened — which is exactly what the stress sweep first
        # showed before this line existed.
        "link_f1_macro": round(_macro([v for v in link_scores.values() if v.support]), 4),
        "legs_scored": sum(1 for v in link_scores.values() if v.support),
        "exceptions": {
            "overall": overall.to_json(),
            "macro_f1": round(macro_f1, 4),
            "per_code": {k: v.to_json() for k, v in sorted(per_code.items())},
        },
        "confusion": {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())},
        "unresolved": {
            "count": len(unresolved),
            "value_at_risk": str(to_rupees(exposure)),
            "by_code": {c: n for c, n in sorted(
                ((f.code.value, sum(1 for x in unresolved if x.code is f.code))
                 for f in unresolved), key=lambda kv: -kv[1])},
        },
        "auto_resolved": {
            "count": sum(1 for f in res.findings
                         if f.disposition is Disposition.AUTO_RESOLVED),
        },
        "tiers": {t: sum(1 for m in res.matches if m.tier.value == t)
                  for t in sorted({m.tier.value for m in res.matches})},
    }
