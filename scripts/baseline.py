"""What this is worth, measured against what a merchant does today.

F1 is not money. The track asks for evidence that the thing creates value, and
the honest way to show that is to reconcile the same books the way they are
reconciled now — an exact-identifier lookup in a spreadsheet, then eyeballing
whatever is left — and report the difference in rupees.

Three configurations over the identical corpus and the identical ground truth:

  exact identifier only   what a VLOOKUP on order_id and UTR finds
  deterministic tiers     + normalised ids, optimal assignment, aggregates
  with the model          + reading references a pattern cannot parse

The number that matters is the last column: the money sitting under records the
configuration could not link, which somebody has to work through by hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.evaluate import _pairs_bank, _pairs_direct, _pairs_erp, _score  # noqa: E402
from kosh.ingest import build_batches, load                              # noqa: E402
from kosh.match import Leg, ReconResult, Tier, reconcile                  # noqa: E402
from kosh.money import fmt                                                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "outputs"


def only_tiers(res: ReconResult, tiers: set) -> ReconResult:
    """The same run, seen as if only some tiers existed."""
    trimmed = ReconResult()
    trimmed.matches = [m for m in res.matches if m.tier in tiers]
    trimmed.findings = list(res.findings)
    return trimmed


def measure(res: ReconResult, ds, gt: dict) -> dict:
    truth = {
        "invoice_to_payment": {(k, v) for k, vs in gt["invoice_to_payment"].items()
                               for v in (vs if isinstance(vs, list) else [vs])},
        "settlement_to_bank": {(s, k) for s, ks in gt["batch_to_bank"].items()
                               for k in ks},
        "invoice_to_bank": {(k, v) for k, v in gt.get("invoice_to_bank", {}).items()},
    }
    found = {"invoice_to_payment": _pairs_erp(res),
             "settlement_to_bank": _pairs_bank(res),
             "invoice_to_bank": _pairs_direct(res)}

    linked, missed_value, missed = set(), 0, 0
    value_of = {i.invoice_no: abs(i.gross_paise) for i in ds.invoices}
    value_of.update({t.entity_id: abs(t.amount_paise) for t in ds.pg})
    value_of.update({b.key: abs(b.amount_paise) for b in ds.bank})
    batch_value = {b.settlement_id: abs(b.net_paise) for b in build_batches(ds)}
    value_of.update(batch_value)

    for leg, pairs in truth.items():
        got = found[leg]
        for a, b in pairs:
            if (a, b) in got:
                linked.update((a, b))
            else:
                missed += 1
                # The money a person now has to account for by hand: the larger
                # side of the link nobody made.
                missed_value += max(value_of.get(a, 0), value_of.get(b, 0))

    total_true = sum(len(v) for v in truth.values())
    scored = {leg: _score(found[leg], truth[leg]).to_json() for leg in truth}
    return {"true_links": total_true, "links_found": total_true - missed,
            "links_missed": missed,
            "recall": round((total_true - missed) / total_true, 4) if total_true else 0,
            "unlinked_value_paise": missed_value,
            "per_leg": scored}


def main() -> None:
    use_llm = "--llm" in sys.argv
    adj = None
    if use_llm:
        from kosh.llm import LocalAdjudicator
        adj = LocalAdjudicator()
        print(f"loading {adj.name} …", flush=True)
        adj.load()

    ds, errors = load(DATA)
    assert not errors, errors
    gt = json.loads((DATA / "ground_truth.json").read_text())
    full = reconcile(ds, build_batches(ds), adj)

    deterministic = {Tier.EXACT_ID, Tier.NORMALIZED_ID, Tier.AMOUNT_DATE,
                     Tier.AGGREGATE}
    configs = [
        ("exact identifier only (a spreadsheet)", only_tiers(full, {Tier.EXACT_ID})),
        ("deterministic tiers", only_tiers(full, deterministic)),
    ]
    if use_llm:
        configs.append(("with the model", full))

    rows = [(name, measure(res, ds, gt)) for name, res in configs]

    print(f"\n{len(ds):,} records · {rows[0][1]['true_links']} links that genuinely exist\n")
    print(f"{'how the books get reconciled':<40} {'links':>7} {'recall':>8} "
          f"{'left by hand':>14}")
    print("-" * 74)
    for name, m in rows:
        print(f"{name:<40} {m['links_found']:>3}/{m['true_links']:<3} "
              f"{m['recall']:>7.1%} {fmt(m['unlinked_value_paise']):>14}")

    base = rows[0][1]
    best = rows[-1][1]
    saved = base["unlinked_value_paise"] - best["unlinked_value_paise"]
    print(f"\n  Against the spreadsheet, this links {best['links_found'] - base['links_found']} "
          f"more of them and takes {fmt(saved)}")
    print(f"  off the desk of whoever would otherwise reconcile it by hand.")

    OUT.mkdir(exist_ok=True)
    name = "baseline_llm" if use_llm else "baseline"
    (OUT / f"{name}.json").write_text(json.dumps(
        {"records": len(ds), "configurations": [
            {"name": n, **m} for n, m in rows],
         "value_recovered_vs_spreadsheet_paise": saved}, indent=2))
    print(f"\nwrote {OUT / f'{name}.json'}")


if __name__ == "__main__":
    main()
