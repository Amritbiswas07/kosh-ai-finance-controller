"""Prove the whole thing runs with no network at all.

`HF_HUB_OFFLINE=1` only asks the hub client to behave, and a warm cache will
make almost anything look offline. This monkey-patches `socket` so that *any*
outbound connection raises, then runs the full pipeline — generate, ingest,
reconcile with the model loaded, adjudicate, evaluate, report.

If a hosted API had crept in anywhere, this fails loudly.
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*a, **k):
    raise NetworkBlocked("outbound network access is blocked by verify_offline.py")


def main() -> int:
    socket.socket.connect = _blocked            # type: ignore[method-assign]
    socket.create_connection = _blocked         # type: ignore[assignment]
    socket.socket.connect_ex = _blocked         # type: ignore[method-assign]
    print("Outbound sockets blocked. Running the full pipeline from local files only.\n")

    from kosh.evaluate import evaluate
    from kosh.generate import build, write
    from kosh.ingest import build_batches, load
    from kosh.llm import LocalAdjudicator
    from kosh.match import Tier, reconcile
    from kosh.position import build_position
    from kosh.report import html_report, markdown_report

    data = ROOT / "data"
    ds, gt, inj = build(seed=20260824)
    write(ds, gt, inj, data, 20260824)
    print(f"  [ok] generated       {len(ds):,} records across three sources")

    adj = LocalAdjudicator()
    took = adj.load()
    print(f"  [ok] model loaded    {adj.name} on {adj.device} in {took:.1f}s")

    t = time.perf_counter()
    ds, errors = load(data)
    batches = build_batches(ds)
    res = reconcile(ds, batches, adj)
    wall = time.perf_counter() - t
    assert not errors, errors
    print(f"  [ok] reconciled      {len(res.matches)} matches, {len(res.findings)} findings "
          f"in {wall:.1f}s")

    adjudicated = [m for m in res.matches if m.tier is Tier.ADJUDICATED]
    print(f"  [ok] adjudicated     {len(adjudicated)} residual(s) settled by the model")

    pos = build_position(ds, batches, res)
    assert pos.residual == 0, f"cash bridge does not balance: {pos.residual}"
    print("  [ok] cash bridge     balances to the paise")

    m = evaluate(res, ds, data / "ground_truth.json", wall)
    e = m["exceptions"]["overall"]
    print(f"  [ok] evaluated       link F1 {m['link_f1_macro']:.4f}, "
          f"exception F1 {e['f1']:.4f}")

    meta = {"model": adj.name, "seed": 20260824,
            "llm_seconds": round(adj.seconds, 1)}
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    (out / "recon.html").write_text(html_report(res, ds, pos, m, meta))
    (out / "recon.md").write_text(markdown_report(res, ds, pos, m, meta))
    print("  [ok] rendered        recon.md and recon.html\n")
    print("PASS: the full pipeline ran with no network access.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
