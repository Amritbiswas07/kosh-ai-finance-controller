"""Command line for Kosh."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .ask import answer
from .evaluate import evaluate
from .generate import Injections, build, write
from .ingest import build_batches, load
from .match import Disposition, reconcile
from .money import fmt
from .position import bridge_rows, build_position
from .report import html_report, json_report, markdown_report

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = ROOT / "outputs"


def _adjudicator(mode: str):
    if mode == "off":
        return None, "no model (deterministic only)"
    from .llm import LocalAdjudicator
    adj = LocalAdjudicator()
    took = adj.load()
    return adj, f"{adj.name} on {adj.device} (loaded in {took:.1f}s)"


def _run(data: Path, llm: str) -> tuple:
    adj, label = _adjudicator(llm)
    t = time.perf_counter()
    ds, errs = load(data)
    batches = build_batches(ds)
    res = reconcile(ds, batches, adj)
    wall = time.perf_counter() - t
    pos = build_position(ds, batches, res)
    return ds, batches, res, pos, wall, errs, adj, label


def cmd_generate(a) -> int:
    inj = Injections()
    ds, gt, inj = build(seed=a.seed, n_orders=a.orders, inj=inj)
    m = write(ds, gt, inj, a.out, a.seed)
    print(f"seed {a.seed} → {a.out}")
    for k, v in m["counts"].items():
        print(f"  {k:22} {v:>6,}")
    print(f"  {'defects injected':22} {m['injected_total']:>6}")
    print(f"  {'ground-truth exceptions':22} {m['ground_truth_exceptions']:>6}")
    print(f"  {'true links':22} {sum(m['true_links'].values()):>6}")
    return 0


def cmd_recon(a) -> int:
    ds, batches, res, pos, wall, errs, adj, label = _run(a.data, a.llm)
    if errs:
        print(f"! {len(errs)} unparseable rows:", *errs[:5], sep="\n  ")

    metrics = None
    gt = a.data / "ground_truth.json"
    if gt.exists() and not a.no_eval:
        metrics = evaluate(res, ds, gt, wall)

    meta = {"seed": a.data.name, "model": label, "period": "synthetic",
            "llm_seconds": round(getattr(adj, "seconds", 0.0), 1) if adj else 0.0}

    print(f"\n{label}")
    print(f"{res.counts['total_records']:,} records · engine "
          f"{res.timings['total_s'] * 1000:.1f} ms · wall {wall:.2f} s"
          + (f" · model {meta['llm_seconds']:.1f} s" if meta["llm_seconds"] else ""))
    print("\nWhere the money is")
    for lbl, amt, kind in bridge_rows(pos):
        subtotal = kind in ("subtotal", "check")
        print(f"  {lbl:36} {fmt(amt, sign=not subtotal):>16}")

    tiers: dict[str, int] = {}
    for m in res.matches:
        tiers[m.tier.value] = tiers.get(m.tier.value, 0) + 1
    print(f"\nMatched {len(res.matches)} · " + " · ".join(f"{k}={v}" for k, v in sorted(tiers.items())))

    unresolved = res.unresolved()
    exposure = sum(abs(f.value_at_risk_paise) for f in unresolved)
    print(f"Findings {len(res.findings)} · {len(unresolved)} need review · "
          f"{fmt(exposure)} exposure")
    for code, n in res.by_code().items():
        need = sum(1 for f in unresolved if f.code.value == code)
        print(f"  {code:28} {n:>3}   {'needs review' if need else 'auto-resolved'}")

    if metrics:
        e = metrics["exceptions"]["overall"]
        print(f"\nMeasured  link F1 {metrics['link_f1_macro']:.4f} · "
              f"exception P {e['precision']:.4f} R {e['recall']:.4f} F1 {e['f1']:.4f} · "
              f"auto-clear {metrics['auto_clear_rate']:.1%} · "
              f"{metrics['records_per_second']:,} rec/s")

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "recon.json").write_text(json_report(res, pos, metrics, meta))
    (a.out / "recon.md").write_text(markdown_report(res, ds, pos, metrics, meta))
    (a.out / "recon.html").write_text(html_report(res, ds, pos, metrics, meta))
    print(f"\nwrote {a.out/'recon.json'}\n      {a.out/'recon.md'}\n      {a.out/'recon.html'}")
    return 0


def cmd_evaluate(a) -> int:
    ds, batches, res, pos, wall, errs, adj, label = _run(a.data, a.llm)
    metrics = evaluate(res, ds, a.data / "ground_truth.json", wall)
    print(json.dumps(metrics, indent=2))
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return 0


def cmd_ask(a) -> int:
    ds, batches, res, pos, wall, errs, adj, label = _run(a.data, a.llm)
    out = answer(" ".join(a.question), res, pos, adj)
    print(f"\nQ: {out['question']}\n\nA: {out['answer']}\n")
    if a.show_facts and out["facts"]:
        print("Grounded in:")
        for f in out["facts"]:
            print(f"  · {f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kosh",
                                 description="AI finance controller: three-way settlement reconciliation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a synthetic three-source corpus + ground truth")
    g.add_argument("--seed", type=int, default=20260824)
    g.add_argument("--orders", type=int, default=140)
    g.add_argument("--out", type=Path, default=DATA)
    g.set_defaults(fn=cmd_generate)

    for name, fn, helptext in (("recon", cmd_recon, "reconcile and write the pack"),
                               ("evaluate", cmd_evaluate, "print metrics as JSON"),
                               ("ask", cmd_ask, "ask a question about the run")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--data", type=Path, default=DATA)
        p.add_argument("--out", type=Path, default=OUT)
        p.add_argument("--llm", choices=("on", "off"), default="off",
                       help="'on' loads the local model for the adjudication tier")
        p.set_defaults(fn=fn)
        if name == "recon":
            p.add_argument("--no-eval", action="store_true",
                           help="skip scoring even if ground truth is present")
        if name == "ask":
            p.add_argument("question", nargs="+")
            p.add_argument("--show-facts", action="store_true")

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
