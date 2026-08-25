"""Accuracy across many seeds, not one.

A reconciliation engine scored on a single generated corpus tells you almost
nothing: the corpus and the engine were written by the same person on the same
afternoon. So this regenerates the whole world from scratch for N seeds and
reports the distribution — mean, min, and every seed that scored below a
perfect run, listed by name so it can be reproduced with
`kosh generate --seed <n>`.

Run with `--llm` to include the adjudication tier (slower, ~10s per seed).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.evaluate import evaluate                     # noqa: E402
from kosh.generate import Injections, build, write     # noqa: E402
from kosh.ingest import build_batches, load            # noqa: E402
from kosh.match import reconcile                       # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs"


def one_seed(seed: int, adj, jitter: bool = True, scale: float = 1.0) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        import random as _r
        inj0 = (Injections.jittered(_r.Random(seed * 7919), scale=scale)
                if jitter else None)
        ds, gt, inj = build(seed=seed, inj=inj0)
        write(ds, gt, inj, root, seed)
        t = time.perf_counter()
        ds, errs = load(root)
        # The corpus now spans currencies, and its answer key contains the
        # exchange differences, so the run has to be given the same rates the
        # generator used.
        from kosh.currency import load_rates
        res = reconcile(ds, build_batches(ds), adj,
                        rates=load_rates(root / "fx_rates.csv") or None)
        wall = time.perf_counter() - t
        m = evaluate(res, ds, root / "ground_truth.json", wall)
    return {"seed": seed, "records": m["records"], "defects": inj.total(),
            "ingest_errors": len(errs),
            "link_f1": m["link_f1_macro"],
            "exc_p": m["exceptions"]["overall"]["precision"],
            "exc_r": m["exceptions"]["overall"]["recall"],
            "exc_f1": m["exceptions"]["overall"]["f1"],
            "macro_f1": m["exceptions"]["macro_f1"],
            "clear": m["auto_clear_rate"],
            "rps": m["records_per_second"],
            "unresolved": m["unresolved"]["count"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=25)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply every defect rate — raise it until the engine breaks")
    ap.add_argument("--no-jitter", action="store_true",
                    help="hold defect rates constant across seeds")
    args = ap.parse_args()

    adj = None
    label = "deterministic only"
    if args.llm:
        from kosh.llm import LocalAdjudicator
        adj = LocalAdjudicator()
        print(f"loading {adj.name} …", flush=True)
        print(f"loaded in {adj.load():.1f}s on {adj.device}", flush=True)
        label = f"{adj.name} (default legs)"

    rows = []
    for i in range(args.seeds):
        seed = args.start + i
        rows.append(one_seed(seed, adj, jitter=not args.no_jitter, scale=args.scale))
        print(f"  seed {seed:<5} link_f1={rows[-1]['link_f1']:.4f} "
              f"exc_f1={rows[-1]['exc_f1']:.4f} clear={rows[-1]['clear']:.1%} "
              f"{rows[-1]['records']} recs @ {rows[-1]['rps']:,}/s", flush=True)

    def stat(key):
        vals = [r[key] for r in rows]
        return {"mean": round(statistics.fmean(vals), 4), "min": round(min(vals), 4),
                "max": round(max(vals), 4),
                "stdev": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0}

    summary = {k: stat(k) for k in
               ("link_f1", "exc_p", "exc_r", "exc_f1", "macro_f1", "clear", "rps")}
    imperfect = [r for r in rows if r["link_f1"] < 1.0 or r["exc_f1"] < 1.0]
    total_records = sum(r["records"] for r in rows)

    print(f"\n{args.seeds} seeds, {total_records:,} records, {label}")
    print(f"{'metric':10} {'mean':>9} {'min':>9} {'max':>9} {'stdev':>9}")
    for k, v in summary.items():
        print(f"{k:10} {v['mean']:>9.4f} {v['min']:>9.4f} {v['max']:>9.4f} {v['stdev']:>9.4f}")
    print(f"\nseeds scoring below a perfect run: {len(imperfect)} of {args.seeds}")
    for r in imperfect:
        print(f"  seed {r['seed']}: link_f1={r['link_f1']:.4f} exc_p={r['exc_p']:.4f} "
              f"exc_r={r['exc_r']:.4f}")

    OUT.mkdir(exist_ok=True)
    name = "benchmark_llm" if args.llm else "benchmark"
    if args.scale != 1.0:
        name += f"_scale{args.scale:g}"
    (OUT / f"{name}.json").write_text(json.dumps(
        {"seeds": args.seeds, "start": args.start, "configuration": label, "defect_scale": args.scale,
         "total_records": total_records, "summary": summary,
         "imperfect_seeds": imperfect, "rows": rows}, indent=2))
    print(f"\nwrote {OUT / f'{name}.json'}")


if __name__ == "__main__":
    main()
