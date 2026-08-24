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
from datetime import datetime

from .match import Disposition, ReconResult
from .money import fmt, to_rupees
from .position import Position, bridge_rows
from .schema import EXCEPTION_MEANING, Dataset, ExceptionCode

TIER_MEANING = {
    "T0_EXACT_ID": "identifier present on both sides",
    "T1_NORMALIZED_ID": "identifier matched after normalisation",
    "T2_AMOUNT_DATE": "no identifier; optimal amount + date assignment",
    "T3_AGGREGATE": "split or consolidated; matched as a group",
    "T4_ADJUDICATED": "model chose among candidates, arithmetic verified",
}


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
        f"reconciled in {res.timings['total_s'] * 1000:.1f} ms"
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
:root{--bg:#fbfaf8;--panel:#fff;--ink:#1b1a18;--muted:#6a6560;--line:#e5e0d8;
--accent:#1f6feb;--good:#0f7b45;--warn:#b45309;--bad:#b42318;--chip:#f2efe9;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141312;
--panel:#1c1b19;--ink:#eceae6;--muted:#9d968d;--line:#2e2c29;--accent:#6ea8fe;
--good:#4ade80;--warn:#fbbf24;--bad:#f87171;--chip:#26241f;}}
:root[data-theme=dark]{--bg:#141312;--panel:#1c1b19;--ink:#eceae6;--muted:#9d968d;
--line:#2e2c29;--accent:#6ea8fe;--good:#4ade80;--warn:#fbbf24;--bad:#f87171;--chip:#26241f;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;}
.wrap{max-width:1120px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:30px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:19px;margin:44px 0 14px;letter-spacing:-.01em}
h3{font-size:14px;margin:26px 0 8px;font-family:ui-monospace,SFMono-Regular,monospace}
.sub{color:var(--muted);margin:0 0 4px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:22px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 17px}
.card .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.card .v{font-size:25px;font-weight:600;margin-top:5px;letter-spacing:-.02em;
font-variant-numeric:tabular-nums}
.card .n{font-size:12px;color:var(--muted);margin-top:3px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.chip{display:inline-block;padding:2px 8px;border-radius:99px;background:var(--chip);
font-size:11px;font-weight:600;letter-spacing:.03em}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.sub-row td{font-weight:650;background:var(--chip)}
.meaning{color:var(--muted);font-size:13px;margin:0 0 10px}
.ev{color:var(--muted);font-size:12px;max-width:340px}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def html_report(res: ReconResult, ds: Dataset, pos: Position,
                metrics: dict | None, meta: dict) -> str:
    unresolved = res.unresolved()
    exposure = sum(abs(f.value_at_risk_paise) for f in unresolved)
    H: list[str] = []
    add = H.append
    add("<title>Kosh Reconciliation Pack</title>")
    add(f"<style>{_CSS}</style><div class=wrap>")
    add(f"<h1>Reconciliation pack</h1><p class=sub>"
        f"{res.counts['total_records']:,} records · three sources · "
        f"engine {res.timings['total_s'] * 1000:.1f} ms"
        + (f" · model {meta['llm_seconds']:.1f} s" if meta.get("llm_seconds") else "")
        + f" · {datetime.now():%d %b %Y %H:%M}</p>")

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
        f"<div class=card><div class=k>{_esc(k)}</div><div class=v>{_esc(v)}</div>"
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
        add(f"<h3>{_esc(code.value)} <span class=chip>{len(items)} · {fmt(total)} · "
            f"{'needs review' if need else 'auto-resolved'}</span></h3>")
        add(f"<p class=meaning>{_esc(EXCEPTION_MEANING[code])}</p>")
        add("<div class=scroll><table><tr><th>Record</th><th class=n>Value</th>"
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
