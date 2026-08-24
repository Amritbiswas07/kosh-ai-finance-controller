"""Turning a run into something a controller will actually read.

Two renderings of one payload: Markdown for the repo and the terminal, HTML for
the dashboard. Both are built from the same `ReconResult`, so they cannot drift.

The ordering is a deliberate argument: cash position first (the question being
asked), then what reconciled, then — at full length, never truncated — what did
not. A report that shows the match rate and hides the exception list is the
failure mode this whole project is arguing against, so the exception section
prints every unresolved item, with its evidence and a proposed action.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from datetime import datetime

from .match import Disposition, ReconResult
from .money import fmt, to_rupees
from .position import Position, bridge_rows
from .schema import EXCEPTION_MEANING, Dataset, ExceptionCode

_LOGO = Path(__file__).resolve().parent / "static" / "razorpay-logo.svg"


def _brandmark() -> str:
    """The supplied mark, inlined so its dark half can follow `currentColor`.

    Inlining also keeps the pack a single self-contained file — it is published
    as an artifact under a CSP that blocks external hosts, so a referenced logo
    would simply not appear.
    """
    if _LOGO.is_file():
        return re.sub(r"<\?xml[^>]*\?>\s*", "", _LOGO.read_text()).strip()
    return "<span class=mark>K</span>"


TIER_MEANING = {
    "T0_EXACT_ID": "identifier present on both sides",
    "T1_NORMALIZED_ID": "identifier matched after normalisation",
    "T2_AMOUNT_DATE": "no identifier; optimal amount + date assignment",
    "T3_AGGREGATE": "split or consolidated; matched as a group",
    "T4_ADJUDICATED": "model chose among candidates, arithmetic verified",
}


def _engine_ms(res: ReconResult, meta: dict) -> float:
    """Deterministic time only. `total_s` spans all four legs including the
    adjudication calls, so quoting it as 'engine' next to a separate 'model'
    figure double-counts the model and overstates the engine by ~1000x."""
    return max(0.0, res.timings["total_s"] - meta.get("llm_seconds", 0.0)) * 1000


def _grouped(res: ReconResult) -> dict[ExceptionCode, list]:
    out: dict[ExceptionCode, list] = {}
    for f in res.findings:
        out.setdefault(f.code, []).append(f)
    return dict(sorted(out.items(),
                       key=lambda kv: -sum(abs(f.value_at_risk_paise) for f in kv[1])))


# ------------------------------------------------------------------ markdown

def markdown_report(res: ReconResult, ds: Dataset, pos: Position,
                    metrics: dict | None, meta: dict) -> str:
    L: list[str] = []
    add = L.append
    add(f"# Reconciliation pack — {meta.get('period', 'synthetic period')}")
    add("")
    add(f"Generated {datetime.now():%Y-%m-%d %H:%M}. "
        f"{res.counts['total_records']:,} source records across three systems, "
        f"reconciled in {_engine_ms(res, meta):.1f} ms of engine time"
        + (f" plus {meta['llm_seconds']:.1f} s of model adjudication."
           if meta.get("llm_seconds") else "."))
    add("")

    add("## Where the money is")
    add("")
    add("| | Amount (INR) |")
    add("|---|---:|")
    for label, amt, kind in bridge_rows(pos):
        cell = f"**{fmt(amt)}**" if kind in ("subtotal", "check") else fmt(amt, sign=True)
        add(f"| {'**' + label + '**' if kind == 'subtotal' else label} | {cell} |")
    add("")
    add(f"Outside the settlement chain: **{fmt(pos.open_receivables)}** of invoices with no "
        f"payment, **{fmt(pos.unbilled_revenue)}** of payments with no invoice, "
        f"**{fmt(pos.unexplained_credits)}** of bank credits nobody can place"
        + (f", and **{fmt(pos.tds_receivable)}** of TDS withheld by customers."
           if pos.tds_receivable else "."))
    add("")

    add("## What reconciled")
    add("")
    add("| Tier | How | Matches |")
    add("|---|---|---:|")
    tiers: dict[str, int] = {}
    for m in res.matches:
        tiers[m.tier.value] = tiers.get(m.tier.value, 0) + 1
    for t in sorted(tiers):
        add(f"| `{t}` | {TIER_MEANING.get(t, '')} | {tiers[t]} |")
    add(f"| | **total** | **{len(res.matches)}** |")
    add("")

    if metrics:
        add("## Measured against ground truth")
        add("")
        add("The engine never reads `ground_truth.json`; only the evaluator does.")
        add("")
        add("| Leg | Precision | Recall | F1 | True links |")
        add("|---|---:|---:|---:|---:|")
        for leg, s in metrics["links"].items():
            add(f"| `{leg}` | {s['precision']:.4f} | {s['recall']:.4f} | {s['f1']:.4f} "
                f"| {s['support']} |")
        e = metrics["exceptions"]["overall"]
        add("")
        add(f"Exception classification, scored strictly over (record, code) pairs: "
            f"**precision {e['precision']:.4f}, recall {e['recall']:.4f}, "
            f"F1 {e['f1']:.4f}** on {e['support']} true exceptions "
            f"({e['tp']} correct, {e['fp']} false positives, {e['fn']} missed).")
        add("")
        add(f"Throughput: **{metrics['records_per_second']:,} records/second** end to end "
            f"including CSV parsing. Auto-clear rate **{metrics['auto_clear_rate']:.1%}** "
            f"({metrics['auto_cleared_records']} of {metrics['records']} records correctly "
            "linked and carrying nothing that needs a human).")
        add("")

    unresolved = res.unresolved()
    exposure = sum(abs(f.value_at_risk_paise) for f in unresolved)
    add("## Exceptions")
    add("")
    add(f"{len(res.findings)} findings. **{len(unresolved)} need a human**, carrying "
        f"**{fmt(exposure)}** of exposure. "
        f"{len(res.findings) - len(unresolved)} were explained and closed automatically.")
    add("")
    for code, items in _grouped(res).items():
        need = [f for f in items if f.disposition is Disposition.NEEDS_REVIEW]
        total = sum(abs(f.value_at_risk_paise) for f in items)
        flag = "needs review" if need else "auto-resolved"
        add(f"### {code.value} — {len(items)} item(s), {fmt(total)} ({flag})")
        add("")
        add(f"*{EXCEPTION_MEANING[code]}*")
        add("")
        add("| Record | Value (INR) | Evidence | Proposed action |")
        add("|---|---:|---|---|")
        for f in sorted(items, key=lambda f: -abs(f.value_at_risk_paise)):
            ev = "; ".join(f"{k}={v}" for k, v in list(f.evidence.items())[:4])
            add(f"| `{f.key}` | {fmt(f.value_at_risk_paise)} | {ev} | {f.proposed_action} |")
        add("")

    if res.adjudications:
        add("## Model adjudications")
        add("")
        add("Every residual the deterministic tiers could not settle, what the model "
            "proposed, and whether the arithmetic accepted it.")
        add("")
        add("| Item | Candidates | Chose | Verdict | Model's reason |")
        add("|---|---|---|---|---|")
        for a in res.adjudications:
            item = a.get("bank_line") or a.get("invoice") or a.get("batch", "?")
            add(f"| `{item}` | {len(a.get('candidates', []))} | `{a.get('chose') or '—'}` "
                f"| {a.get('verdict', 'declined')} | {a.get('reason', '')[:120]} |")
        add("")
    return "\n".join(L)


# ---------------------------------------------------------------------- html

_CSS = """
/* Razorpay's live palette, sampled from razorpay.com: #305EFF primary, #132644
   navy ink, #F0F4F6 surface, #ED2939 red, and their fully-rounded pills. Same
   tokens as the web UI, so the pack and the app read as one product. */
:root{
  --paper:#fff; --panel:#fff; --panel-2:#f0f4f6;
  --ink:#132644; --muted:#5a6b84; --line:#e3e9ef; --line-2:#eef2f6;
  --brand:#305eff; --brand-tint:rgba(48,94,255,.09); --brand-ink:#fff;
  --good:#12855c; --warn:#b26a00; --bad:#ed2939;
  --stripe-review:#b26a00; --stripe-auto:#12855c; --pill:40px;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --paper:#0b1424; --panel:#101c30; --panel-2:#16253c;
  --ink:#e8eef7; --muted:#8ea3c0; --line:#22344f; --line-2:#1a2a41;
  --brand:#6e9bff; --brand-tint:rgba(110,155,255,.14); --brand-ink:#0b1424;
  --good:#3fcf8e; --warn:#e8a33d; --bad:#ff6b78;
  --stripe-review:#e8a33d; --stripe-auto:#3fcf8e;
}}
:root[data-theme="dark"]{
  --paper:#0b1424; --panel:#101c30; --panel-2:#16253c;
  --ink:#e8eef7; --muted:#8ea3c0; --line:#22344f; --line-2:#1a2a41;
  --brand:#6e9bff; --brand-tint:rgba(110,155,255,.14); --brand-ink:#0b1424;
  --good:#3fcf8e; --warn:#e8a33d; --bad:#ff6b78;
  --stripe-review:#e8a33d; --stripe-auto:#3fcf8e;
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Mona Sans",ui-sans-serif,-apple-system,"Segoe UI",Inter,Roboto,
    Helvetica,Arial,sans-serif;
  font-size:15px; line-height:1.55; letter-spacing:-.005em;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1140px;margin:0 auto;padding:48px 24px 96px}

.mast{display:flex;align-items:center;gap:13px;border-bottom:1px solid var(--line);
  padding-bottom:20px;margin-bottom:26px;flex-wrap:wrap}
.mark{width:34px;height:34px;border-radius:9px;background:var(--brand);
  color:var(--brand-ink);display:grid;place-items:center;font-weight:800;
  font-size:17px;letter-spacing:-.03em;flex:none}
.mast .rzp{height:26px;width:auto;color:var(--ink);flex:none}
.rule{width:1px;height:30px;background:var(--line);flex:none}
.masthead{flex:1 1 320px}
.eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:var(--brand);margin:0 0 5px}
h1{font-weight:700;font-size:33px;line-height:1.08;letter-spacing:-.03em;margin:0;
  text-wrap:balance}
.sub{color:var(--muted);margin:9px 0 0;font-size:13px;font-variant-numeric:tabular-nums}

h2{font-weight:700;font-size:21px;letter-spacing:-.02em;margin:44px 0 13px;
  text-wrap:balance}
h3{font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;font-weight:600;
  margin:28px 0 8px;display:flex;flex-wrap:wrap;align-items:center;gap:9px}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));
  gap:12px;margin:0 0 8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;display:flex;flex-direction:column;gap:5px}
.card.flag{background:var(--brand-tint);border-color:transparent}
.card .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--muted);font-weight:700}
.card .v{font-size:28px;font-weight:700;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums;line-height:1.05}
.card .n{font-size:12px;color:var(--muted)}

.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;
  background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:10px 15px;border-bottom:1px solid var(--line-2);
  vertical-align:top}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  font-weight:700;white-space:nowrap;background:var(--panel-2);
  border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.sub-row td{font-weight:700;background:var(--brand-tint)}

.chip{display:inline-block;padding:3px 11px;border-radius:var(--pill);font-size:10.5px;
  font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.chip.review{background:var(--warn);color:#fff}
.chip.auto{background:var(--good);color:#fff}
.chip.count{background:var(--panel-2);color:var(--muted);border:1px solid var(--line)}
.block{border-left:3px solid var(--stripe-auto)}
.block.review{border-left-color:var(--stripe-review)}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.meaning{color:var(--muted);font-size:13.5px;margin:0 0 10px;max-width:68ch}
.ev{color:var(--muted);font-size:12px;max-width:330px;line-height:1.5}

footer{margin-top:60px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12.5px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def html_report(res: ReconResult, ds: Dataset, pos: Position,
                metrics: dict | None, meta: dict) -> str:
    unresolved = res.unresolved()
    exposure = sum(abs(f.value_at_risk_paise) for f in unresolved)
    H: list[str] = []
    add = H.append
    add("<title>Settlement Reconciliation Pack</title>")
    add(f"<style>{_CSS}</style><div class=wrap>")
    add(f"<div class=mast>{_brandmark()}<span class=rule></span><div class=masthead>"
        "<p class=eyebrow>Kosh &middot; Razorpay AI Buildathon &middot; Track 4</p>"
        "<h1>Settlement reconciliation pack</h1>"
        f"<p class=sub>{res.counts['total_records']:,} records across ERP, gateway and "
        f"bank &middot; engine {_engine_ms(res, meta):.1f} ms"
        + (f" &middot; model {meta['llm_seconds']:.1f} s" if meta.get("llm_seconds") else "")
        + f" &middot; {datetime.now():%d %b %Y %H:%M}</p></div></div>")

    cards = [("Net settled", fmt(pos.settled_net), "into gateway batches"),
             ("Landed in bank", fmt(pos.landed_in_bank), "traced to a credit"),
             ("In transit", fmt(pos.in_transit), "settled, not yet received"),
             ("Needs review", str(len(unresolved)), f"{fmt(exposure)} of exposure")]
    if metrics:
        cards.append(("Auto-clear rate", f"{metrics['auto_clear_rate']:.1%}",
                      f"{metrics['records_per_second']:,} rec/s"))
        cards.append(("Exception F1", f"{metrics['exceptions']['overall']['f1']:.3f}",
                      "vs held-out ground truth"))
    add("<div class=cards>" + "".join(
        f"<div class='card{' flag' if k == 'Needs review' else ''}'>"
        f"<div class=k>{_esc(k)}</div><div class=v>{_esc(v)}</div>"
        f"<div class=n>{_esc(n)}</div></div>" for k, v, n in cards) + "</div>")

    add("<h2>Where the money is</h2><div class=scroll><table>")
    add("<tr><th>Line</th><th class=n>Amount (INR)</th></tr>")
    for label, amt, kind in bridge_rows(pos):
        cls = " class=sub-row" if kind in ("subtotal", "check") else ""
        colour = (" class='n bad'" if kind == "warn" and amt
                  else " class='n good'" if kind == "ok" else " class=n")
        add(f"<tr{cls}><td>{_esc(label)}</td><td{colour}>"
            f"{_esc(fmt(amt, sign=kind not in ('subtotal', 'check')))}</td></tr>")
    add("</table></div>")

    add("<h2>What reconciled</h2><div class=scroll><table>")
    add("<tr><th>Tier</th><th>How</th><th class=n>Matches</th></tr>")
    tiers: dict[str, int] = {}
    for m in res.matches:
        tiers[m.tier.value] = tiers.get(m.tier.value, 0) + 1
    for t in sorted(tiers):
        add(f"<tr><td><code>{_esc(t)}</code></td><td>{_esc(TIER_MEANING.get(t, ''))}</td>"
            f"<td class=n>{tiers[t]}</td></tr>")
    add(f"<tr class=sub-row><td>total</td><td></td><td class=n>{len(res.matches)}</td></tr>")
    add("</table></div>")

    if metrics:
        add("<h2>Measured against ground truth</h2>")
        add("<p class=meaning>The engine never reads the ground-truth file; only the "
            "evaluator does.</p><div class=scroll><table>")
        add("<tr><th>Leg</th><th class=n>Precision</th><th class=n>Recall</th>"
            "<th class=n>F1</th><th class=n>True links</th></tr>")
        for leg, s in metrics["links"].items():
            add(f"<tr><td><code>{_esc(leg)}</code></td><td class=n>{s['precision']:.4f}</td>"
                f"<td class=n>{s['recall']:.4f}</td><td class=n>{s['f1']:.4f}</td>"
                f"<td class=n>{s['support']}</td></tr>")
        e = metrics["exceptions"]["overall"]
        add(f"<tr class=sub-row><td>exception classification</td>"
            f"<td class=n>{e['precision']:.4f}</td><td class=n>{e['recall']:.4f}</td>"
            f"<td class=n>{e['f1']:.4f}</td><td class=n>{e['support']}</td></tr>")
        add("</table></div>")

    add(f"<h2>Exceptions</h2><p class=meaning>{len(res.findings)} findings; "
        f"<strong>{len(unresolved)} need a human</strong>, carrying {fmt(exposure)} of "
        f"exposure. {len(res.findings) - len(unresolved)} closed automatically. "
        "Every item is listed — nothing is truncated.</p>")
    for code, items in _grouped(res).items():
        need = any(f.disposition is Disposition.NEEDS_REVIEW for f in items)
        total = sum(abs(f.value_at_risk_paise) for f in items)
        add(f"<h3>{_esc(code.value)}"
            f"<span class='chip {'review' if need else 'auto'}'>"
            f"{'needs review' if need else 'auto-resolved'}</span>"
            f"<span class='chip count'>{len(items)} &middot; {fmt(total)}</span></h3>")
        add(f"<p class=meaning>{_esc(EXCEPTION_MEANING[code])}</p>")
        add(f"<div class='scroll block{' review' if need else ''}'><table>"
            "<tr><th>Record</th><th class=n>Value</th>"
            "<th>Evidence</th><th>Proposed action</th></tr>")
        for f in sorted(items, key=lambda f: -abs(f.value_at_risk_paise)):
            ev = "<br>".join(f"{_esc(k)}=<span class=mono>{_esc(v)}</span>"
                             for k, v in list(f.evidence.items())[:5])
            add(f"<tr><td><code>{_esc(f.key)}</code></td>"
                f"<td class=n>{_esc(fmt(f.value_at_risk_paise))}</td>"
                f"<td class=ev>{ev}</td><td>{_esc(f.proposed_action)}</td></tr>")
        add("</table></div>")

    if res.adjudications:
        add("<h2>Model adjudications</h2><p class=meaning>What the deterministic tiers "
            "could not settle, what the model proposed, and whether the arithmetic "
            "accepted it.</p><div class=scroll><table>")
        add("<tr><th>Item</th><th class=n>Candidates</th><th>Chose</th><th>Verdict</th>"
            "<th>Model's reason</th></tr>")
        for a in res.adjudications:
            item = a.get("bank_line") or a.get("invoice") or a.get("batch", "?")
            v = a.get("verdict", "declined")
            cls = "good" if v == "accepted" else "bad" if "rejected" in v else "warn"
            add(f"<tr><td><code>{_esc(item)}</code></td>"
                f"<td class=n>{len(a.get('candidates', []))}</td>"
                f"<td><code>{_esc(a.get('chose') or '—')}</code></td>"
                f"<td class={cls}>{_esc(v)}</td>"
                f"<td class=ev>{_esc(a.get('reason', '')[:160])}</td></tr>")
        add("</table></div>")

    add(f"<footer>Kosh · deterministic tiers T0–T3 plus verified model adjudication at T4 · "
        f"seed {_esc(meta.get('seed', '—'))} · "
        f"{_esc(meta.get('model', 'no model loaded'))}</footer></div>")
    return "\n".join(H)


def json_report(res: ReconResult, pos: Position, metrics: dict | None, meta: dict) -> str:
    return json.dumps({
        "meta": meta, "counts": res.counts, "timings": res.timings,
        "position": pos.to_json(),
        "matches": [m.to_json() for m in res.matches],
        "findings": [f.to_json() for f in res.findings],
        "adjudications": res.adjudications,
        "metrics": metrics,
    }, indent=2)
