"""Does the model earn its place, and on which legs?

Runs the same data three ways and prints the metrics side by side. The claim in
`match.DEFAULT_ADJUDICATED_LEGS` — that legs A and C should not consult a model
— rests on this table, not on taste.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.evaluate import evaluate                                  # noqa: E402
from kosh.ingest import build_batches, load                         # noqa: E402
from kosh.match import (ALL_ADJUDICATED_LEGS, DEFAULT_ADJUDICATED_LEGS,  # noqa: E402
                        reconcile)
from kosh.llm import LocalAdjudicator                               # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "outputs"


def run(label: str, adj, legs) -> dict:
    ds, errs = load(DATA)
    assert not errs, errs
    batches = build_batches(ds)
    if adj is not None:
        adj.calls, adj.seconds = 0, 0.0
    t = time.perf_counter()
    res = reconcile(ds, batches, adj, legs)
    wall = time.perf_counter() - t
    m = evaluate(res, ds, DATA / "ground_truth.json", wall)
    verdicts: dict[str, int] = {}
    for a in res.adjudications:
        v = a.get("verdict", "declined" if a.get("chose") is None else "accepted")
        verdicts[v] = verdicts.get(v, 0) + 1
    return {"label": label, "metrics": m, "llm_calls": getattr(adj, "calls", 0),
            "llm_seconds": round(getattr(adj, "seconds", 0.0), 1), "verdicts": verdicts}


def main() -> None:
    adj = LocalAdjudicator()
    print(f"loading {adj.name} …", flush=True)
    load_s = adj.load()
    print(f"loaded in {load_s:.1f}s on {adj.device}\n", flush=True)

    rows = [
        run("deterministic only (no model)", None, frozenset()),
        run("model on every leg", adj, ALL_ADJUDICATED_LEGS),
        run("model on invoice→bank only (default)", adj, DEFAULT_ADJUDICATED_LEGS),
    ]

    hdr = (f"| {'configuration':38} | link F1 | exc P | exc R | exc F1 | cleared | "
           f"LLM calls | LLM s |")
    sep = "|" + "|".join("-" * w for w in (40, 9, 7, 7, 8, 9, 11, 7)) + "|"
    lines = [hdr, sep]
    for r in rows:
        m, e = r["metrics"], r["metrics"]["exceptions"]["overall"]
        lines.append(
            f"| {r['label']:38} | {m['link_f1_macro']:7.4f} | {e['precision']:5.3f} | "
            f"{e['recall']:5.3f} | {e['f1']:6.4f} | {m['auto_clear_rate']:8.1%} | "
            f"{r['llm_calls']:9d} | {r['llm_seconds']:5.1f} |")
    table = "\n".join(lines)
    print(table)
    print()
    for r in rows:
        print(f"{r['label']:40} verdicts={r['verdicts']}")

    OUT.mkdir(exist_ok=True)
    (OUT / "ablation.json").write_text(json.dumps(
        {"model": adj.name, "device": adj.device, "load_seconds": round(load_s, 1),
         "rows": rows}, indent=2))
    (OUT / "ablation.md").write_text(
        f"# Does the model earn its place?\n\n"
        f"`{adj.name}` on {adj.device}, loaded in {load_s:.1f}s. Same data, same seed, "
        f"three configurations.\n\n{table}\n\n"
        + "\n".join(f"- **{r['label']}** — adjudication verdicts: `{r['verdicts']}`"
                    for r in rows) + "\n")
    print(f"\nwrote {OUT / 'ablation.md'}")


if __name__ == "__main__":
    main()
