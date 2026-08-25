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
from .match import reconcile
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


def _run(data: Path, llm: str, confirmed=None, rules=None) -> tuple:
    adj, label = _adjudicator(llm)
    t = time.perf_counter()
    ds, errs = load(data)
    batches = build_batches(ds)
    res = reconcile(ds, batches, adj, confirmed=confirmed, rules=rules)
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

    manifest = a.data / "manifest.json"
    seed = (json.loads(manifest.read_text()).get("seed", "unknown")
            if manifest.exists() else "unknown")
    meta = {"seed": seed, "model": label, "period": "synthetic",
            "llm_seconds": round(getattr(adj, "seconds", 0.0), 3) if adj else 0.0}

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


def cmd_pull(a) -> int:
    """Fetch a real settlement recon period and write it where the engine reads."""
    from .razorpay import MissingCredentials, RazorpayClient, RazorpayError, to_pg_txns, write_csv
    try:
        client = RazorpayClient.from_env()
    except MissingCredentials as exc:
        print(f"\n{exc}\n")
        print("  export RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx")
        print("  export RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxx\n")
        print("Kosh never stores or prints them; they are read from the environment")
        print("and sent only in the Authorization header.")
        return 2

    period = f"{a.year}-{a.month:02d}" + (f"-{a.day:02d}" if a.day else "")
    print(f"GET {client.base}/settlements/recon/combined  ({period}) …")
    try:
        items = client.fetch_recon(a.year, a.month, a.day, limit=a.limit)
    except RazorpayError as exc:
        print(f"\n  {exc}\n")
        return 1

    rows, errors = to_pg_txns(items)
    print(f"  {len(items):,} recon rows · {len(rows):,} mapped"
          + (f" · {len(errors)} unmappable" if errors else ""))
    for e in errors[:5]:
        print(f"    ! {e}")
    if not rows:
        print("\n  Nothing settled in that period. Try a different month, or "
              "--day to narrow it.")
        return 0

    a.out.mkdir(parents=True, exist_ok=True)
    target = a.out / "pg_settlement_report.csv"
    write_csv(rows, target)
    gross = sum(t.amount_paise for t in rows if t.type == "payment")
    fees = sum(t.fee_paise + t.tax_paise for t in rows)
    print(f"\n  captured {fmt(gross)} · fees and GST {fmt(fees)} · "
          f"{len({t.settlement_id for t in rows if t.settlement_id})} settlement(s)")
    print(f"  wrote {target}")
    print("\nThe pulled period is written in the same shape the generator emits,")
    print("so `kosh recon` reads it with the same parser. Supply the matching")
    print("ERP and bank exports in the same directory to reconcile it.")
    return 0


def cmd_sync(a) -> int:
    from .store import Store
    store = Store(a.db)
    confirmed = store.manual_links()
    rules = store.rules(enabled_only=True)
    ds, batches, res, pos, wall, errs, adj, label = _run(a.data, a.llm, confirmed,
                                                        rules)
    if rules:
        print(f"  applying {len(rules)} rule(s) a controller stated")
    if confirmed:
        print(f"  replaying {len(confirmed)} link(s) a person already confirmed")
    rep = store.sync(ds, res)
    before = store.counts()
    store.close()

    print(f"\nrun {rep.run_id} · {rep.records:,} records "
          f"({rep.new_records:,} new or amended) · {rep.new_links} new links")
    if rep.opened:
        print(f"\n  opened {len(rep.opened)}")
        for k, c, v in sorted(rep.opened, key=lambda x: -abs(x[2]))[:a.top]:
            print(f"    + {k:<22} {c:<28} {fmt(v):>14}")
    if rep.resolved:
        print(f"\n  cleared {len(rep.resolved)}")
        for k, c, v, how in sorted(rep.resolved, key=lambda x: -abs(x[2])):
            print(f"    - {k:<22} {c:<28} {fmt(v):>14}   {how}")
    if rep.vanished:
        print(f"\n  !! {len(rep.vanished)} open break(s) whose record left the data")
        print("     kept open: absence from an export is not a resolution")
        for k, c, v in sorted(rep.vanished, key=lambda x: -abs(x[2]))[:a.top]:
            print(f"    ? {k:<22} {c:<28} {fmt(v):>14}")
    if rep.carried:
        print(f"\n  still open {len(rep.carried)} "
              f"(oldest {max(a2 for *_, a2 in rep.carried)} run(s) ago)")
        for k, c, v, age in sorted(rep.carried, key=lambda x: (-x[3], -abs(x[2])))[:a.top]:
            print(f"    · {k:<22} {c:<28} {fmt(v):>14}   {age} run(s) old")
    print(f"\n  ledger: {before}")
    return 0


#: Few-shot examples. The third exists because a 1.5B model reads "mentions the
#: customer's name" as the literal string "customer" unless it has seen the
#: boolean field used; the backtest caught that, twice, before these were added.
RULE_EXAMPLES = """Examples

Instruction: link a credit if the narration mentions ACME and it is within 2 percent of the invoice
JSON: {"name": "acme-direct", "when": [{"field": "narration", "op": "contains", "value": "ACME"}, {"field": "amount_gap_pct", "op": "at_most", "value": 2}]}

Instruction: only link credits over 50000 rupees that landed within a week of the invoice
JSON: {"name": "large-and-prompt", "when": [{"field": "credit_amount", "op": "at_least", "value": 50000}, {"field": "days_after_invoice", "op": "at_most", "value": 7}]}

Instruction: if the narration mentions the customer's own name and the amount is within 4 percent, link it
JSON: {"name": "customer-named", "when": [{"field": "narration_mentions_customer", "op": "is_true"}, {"field": "amount_gap_pct", "op": "at_most", "value": 4}]}

Instruction: link a credit that does not mention REVERSAL and is within 1 percent of the invoice
JSON: {"name": "not-a-reversal", "when": [{"field": "narration", "op": "not_contains", "value": "REVERSAL"}, {"field": "amount_gap_pct", "op": "at_most", "value": 1}]}
"""


def cmd_rule(a) -> int:
    """State a rule in English, see what it compiled to, and what it would do."""
    import json as _json

    from .rules import Rule, RuleError, backtest, catalogue, parse_compiled
    from .store import Store

    store = Store(a.db)
    try:
        if a.action == "list":
            rules = store.rules()
            if not rules:
                print("  no rules yet — `kosh rule add \"...\"` states one")
            for r in rules:
                mark = "on " if r.enabled else "off"
                print(f"\n  [{mark}] {r.describe()}")
                if r.source_text:
                    print(f"        stated as: {r.source_text}")
                if r.backtest:
                    b = r.backtest
                    print(f"        backtest: {b.get('correct', 0)} correct, "
                          f"{b.get('wrong', 0)} wrong, "
                          f"precision {b.get('precision', 0):.0%}")
            return 0

        if a.action in ("enable", "disable"):
            store.set_rule_enabled(a.name, a.action == "enable", a.by or "unknown")
            print(f"  {a.name} {a.action}d")
            return 0

        # --- stating a rule --------------------------------------------------
        if a.action == "add-json":
            rule = Rule.from_json(_json.loads(a.text))
            rule.author = a.by or rule.author
        else:
            from .llm import LocalAdjudicator
            adj = LocalAdjudicator()
            print(f"compiling with {adj.name} …", flush=True)
            adj.load()
            raw = adj.compile_rule(a.text, catalogue(), RULE_EXAMPLES)
            rule = parse_compiled(raw, author=a.by or "unknown", source_text=a.text)

        print("\nThe model read that as a rule. It decided nothing; this is what")
        print("will be evaluated, by arithmetic, on every run:\n")
        print("  " + rule.describe().replace("\n", "\n  "))

        # --- backtest against books whose answers are known ------------------
        ds, errs = load(a.data)
        gt_path = a.data / "ground_truth.json"
        if not gt_path.exists():
            print("\n  No history to test it against; saved but left off.")
            store.save_rule(rule, a.by or "unknown")
            return 0
        truth = _json.loads(gt_path.read_text()).get("invoice_to_bank", {})
        res = reconcile(ds, build_batches(ds))
        unpaid = [i for i in ds.invoices
                  if any(f.key == i.invoice_no and f.code.value == "UNPAID_INVOICE"
                         for f in res.findings)]
        credits = [b for b in ds.bank
                   if any(f.key == b.key and f.code.value == "UNEXPECTED_BANK_CREDIT"
                          for f in res.findings)]
        bt = backtest(rule, unpaid, credits, truth)
        rule.backtest = bt.to_json()

        print(f"\nBacktested over {len(unpaid)} unmatched invoice(s) against "
              f"{len(credits)} unexplained credit(s):")
        print(f"  {bt.verdict()}")
        for pair in bt.wrong[:3]:
            print(f"    wrong: {pair[0]} -> {pair[1]}")

        if bt.precision >= 1.0 and bt.proposed and a.enable:
            rule.enabled = True
            store.save_rule(rule, a.by or "unknown")
            print(f"\n  Enabled. Later runs apply it before anything guesses.")
        else:
            store.save_rule(rule, a.by or "unknown")
            reason = ("it links nothing" if not bt.proposed
                      else "it would mislink" if bt.precision < 1.0
                      else "pass --enable to switch it on")
            print(f"\n  Saved but left off: {reason}.")
        return 0
    except (RuleError, KeyError, ValueError) as exc:
        print(f"\n  {exc}\n")
        return 1
    finally:
        store.close()


def cmd_exception(a) -> int:
    """Work an exception: assign it, note it, close it, or link it by hand."""
    from .store import APPROVAL_THRESHOLD_PAISE, Store
    store = Store(a.db)
    try:
        if a.action == "list":
            rows = store.db.execute(
                "SELECT key, code, status, value_at_risk, assignee, note FROM exception "
                "WHERE status IN ('open','investigating') "
                "ORDER BY ABS(value_at_risk) DESC LIMIT ?", (a.top,)).fetchall()
            if not rows:
                print("  nothing open")
            for key, code, status, val, who, note in rows:
                print(f"  {key:<22} {code:<26} {fmt(val or 0):>13}  {status:<14}"
                      f"{('→ ' + who) if who else ''}")
                if note:
                    print(f"      note: {note}")
        elif a.action == "assign":
            store.assign(a.key, a.code, a.to, a.by)
            print(f"  {a.key}/{a.code} assigned to {a.to}")
        elif a.action == "note":
            store.annotate(a.key, a.code, a.note, a.by)
            print(f"  noted on {a.key}/{a.code}")
        elif a.action in ("resolve", "write-off"):
            status = "resolved" if a.action == "resolve" else "written_off"
            store.resolve(a.key, a.code, a.by, a.note, status, a.approved_by)
            print(f"  {a.key}/{a.code} {status} by {a.by}"
                  + (f", approved by {a.approved_by}" if a.approved_by else ""))
        elif a.action == "link":
            store.confirm_link(a.leg, a.key, a.to, a.by, a.note)
            print(f"  confirmed {a.key} -> {a.to} on {a.leg}")
            print("  later runs replay this; the engine will not ask again.")
        elif a.action == "history":
            for at, actor, action, subject, detail in store.history(a.top):
                print(f"  {at}  {actor:<12} {action:<14} {subject:<30} {detail or ''}")
    except (KeyError, ValueError, PermissionError) as exc:
        print(f"\n  {exc}\n")
        if isinstance(exc, PermissionError):
            print(f"  Resolutions at or above {fmt(APPROVAL_THRESHOLD_PAISE)} need "
                  "--approved-by, and it cannot be the same person.")
        store.close()
        return 1
    store.close()
    return 0


def cmd_serve(a) -> int:
    from .server import serve
    serve(a.data, host=a.host, port=a.port, preload=not a.no_preload,
          db=None if a.no_ledger else a.db)
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
                               ("ask", cmd_ask, "ask a question about the run"),
                               ("serve", cmd_serve, "browse the run in a local web UI"),
                               ("sync", cmd_sync,
                                "reconcile and fold the result into the running ledger"),
                               ("pull", cmd_pull,
                                "fetch a real settlement recon period from Razorpay"),
                               ("exception", cmd_exception,
                                "work an exception: assign, note, resolve, link"),
                               ("rule", cmd_rule,
                                "state a matching rule in English; see it compiled "
                                "and backtested")):
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
        if name == "pull":
            # A pulled period belongs beside the other source files, not in the
            # reports directory the other commands write to.
            p.set_defaults(out=DATA)
            p.add_argument("--year", type=int, required=True)
            p.add_argument("--month", type=int, required=True)
            p.add_argument("--day", type=int, default=None)
            p.add_argument("--limit", type=int, default=None)
        if name == "serve":
            p.add_argument("--db", type=Path, default=ROOT / "outputs" / "kosh.db")
            p.add_argument("--no-ledger", action="store_true",
                           help="run without the accumulated ledger")
        if name in ("sync", "exception", "rule"):
            p.add_argument("--db", type=Path, default=ROOT / "outputs" / "kosh.db")
            p.add_argument("--top", type=int, default=8)
        if name == "rule":
            p.add_argument("action",
                           choices=("add", "add-json", "list", "enable", "disable"))
            p.add_argument("text", nargs="?", default="",
                           help="the instruction, in plain English")
            p.add_argument("--name", default="")
            p.add_argument("--by", default="")
            p.add_argument("--enable", action="store_true",
                           help="switch it on if the backtest is clean")
        if name == "exception":
            p.add_argument("action", choices=("list", "assign", "note", "resolve",
                                              "write-off", "link", "history"))
            p.add_argument("--key", default="")
            p.add_argument("--code", default="")
            p.add_argument("--to", default="", help="assignee, or the far side of a link")
            p.add_argument("--leg", default="settlement_to_bank")
            p.add_argument("--by", default="", help="who is doing this")
            p.add_argument("--note", default="")
            p.add_argument("--approved-by", dest="approved_by", default=None)
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
            p.add_argument("--no-preload", action="store_true",
                           help="start with no run loaded instead of a deterministic one")

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
