"""Score the engine on data written against it.

The metric that matters here is not coverage. It is whether the engine, faced
with a break it has no code for, admits it — or picks the nearest label and
states a cause it cannot know. That second thing is the dangerous behaviour in
a finance tool, so it gets a name and a number: the **confabulation rate**.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.adversary import build                                   # noqa: E402
from kosh.ingest import build_batches                              # noqa: E402
from kosh.match import Leg, Tier, reconcile                        # noqa: E402
from kosh.schema import ExceptionCode                              # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs"
HONEST = {ExceptionCode.UNCLASSIFIED.value}

#: Codes that assert only an absence — "no credit arrived", "nothing explains
#: this". On a break the taxonomy has no code for, these are *true statements
#: that miss the connection*, which is a limitation. It is not the same failure
#: as naming a cause the engine cannot know, so it is not scored as one.
ABSENCE = {ExceptionCode.MISSING_IN_BANK.value,
           ExceptionCode.UNEXPECTED_BANK_CREDIT.value,
           ExceptionCode.UNPAID_INVOICE.value,
           ExceptionCode.UNBILLED_PAYMENT.value,
           ExceptionCode.FUNDS_ON_HOLD.value}


def bank_pairs(res):
    out = set()
    for m in res.matches:
        if m.leg is not Leg.BATCH_BANK:
            continue
        if m.left.startswith("bank:"):
            out.update((sid, m.left) for sid in m.right)
        else:
            out.update((m.left, k) for k in m.right)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    args = ap.parse_args()

    adj, label = None, "deterministic only"
    if args.llm:
        from kosh.llm import LocalAdjudicator
        adj = LocalAdjudicator()
        print(f"loading {adj.name} …", flush=True)
        print(f"loaded in {adj.load():.1f}s on {adj.device}\n", flush=True)
        label = f"{adj.name}"

    ds, cases = build()
    t = time.perf_counter()
    res = reconcile(ds, build_batches(ds), adj)
    wall = time.perf_counter() - t

    found = bank_pairs(res)
    truth = {p for c in cases for p in c.links}
    by_key: dict[str, list] = {}
    for f in res.findings:
        by_key.setdefault(f.key, []).append(f)

    rows, confab, admitted, recovered, missed, false_links = [], [], [], [], [], []
    partial = []
    for c in cases:
        codes = sorted({f.code.value for k in c.keys for f in by_key.get(k, [])})
        if c.kind == "link":
            ok = all(p in found for p in c.links)
            (recovered if ok else missed).append(c.name)
            verdict = "recovered" if ok else "missed"
        elif c.kind == "no_link":
            bad = [p for p in found if p[1] in c.keys and p not in truth]
            if bad:
                false_links.append(c.name)
            verdict = "false link" if bad else "correctly declined"
        else:                                    # unknown
            if not codes:
                verdict, _ = "silent", admitted.append(c.name)
            elif set(codes) <= HONEST:
                verdict, _ = "admitted unknown", admitted.append(c.name)
            elif set(codes) <= ABSENCE:
                verdict, _ = "partial (true, unlinked)", partial.append((c.name, codes))
            else:
                verdict, _ = "INVENTED A CAUSE", confab.append((c.name, codes))
        rows.append({"case": c.name, "kind": c.kind, "verdict": verdict,
                     "codes": codes, "why": c.why})

    unknowns = [c for c in cases if c.kind == "unknown"]
    links = [c for c in cases if c.kind == "link"]
    nolinks = [c for c in cases if c.kind == "no_link"]

    print(f"{'case':<24} {'expected':<9} {'verdict':<18} codes")
    print("-" * 88)
    for r in rows:
        print(f"{r['case']:<24} {r['kind']:<9} {r['verdict']:<18} "
              f"{','.join(r['codes']) or '—'}")

    rate = len(confab) / len(unknowns) if unknowns else 0.0
    print(f"\n{label} · {len(ds)} records · {wall:.2f}s")
    print(f"  links recovered      {len(recovered)}/{len(links)}")
    print(f"  false links created  {len(false_links)}/{len(nolinks)}")
    print(f"  unknowns admitted    {len(admitted)}/{len(unknowns)}")
    print(f"  partial (true but unlinked) {len(partial)}/{len(unknowns)}"
          + (f"  → {[c for c, _ in partial]}" if partial else ""))
    print(f"  INVENTED-CAUSE RATE  {rate:.0%}"
          + (f"  → {confab}" if confab else "   (never named a cause it cannot know)"))
    if missed:
        print(f"  missed links         {missed}")

    OUT.mkdir(exist_ok=True)
    name = "adversarial_llm" if args.llm else "adversarial"
    (OUT / f"{name}.json").write_text(json.dumps(
        {"configuration": label, "records": len(ds), "seconds": round(wall, 2),
         "links_recovered": len(recovered), "links_total": len(links),
         "false_links": len(false_links), "no_link_cases": len(nolinks),
         "unknowns_admitted": len(admitted), "unknowns_total": len(unknowns),
         "invented_cause_rate": round(rate, 4), "invented_causes": confab,
         "partial_true_but_unlinked": partial,
         "missed_links": missed,
         "tiers": {t: sum(1 for m in res.matches if m.tier.value == t)
                   for t in sorted({m.tier.value for m in res.matches})},
         "cases": rows}, indent=2))
    print(f"\nwrote {OUT / f'{name}.json'}")


if __name__ == "__main__":
    main()
