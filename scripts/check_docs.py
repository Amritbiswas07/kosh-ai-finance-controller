"""Verify that every load-bearing number in the documentation is still true.

Written because the same mistake happened three times: the code moved, the
prose did not, and the suite stayed green because nothing had ever compared
them. A stale figure in a README is not a typo — it is a claim about the
system that is no longer true, and it is exactly what a reader checks first.

Run it after any change that could move a number. It is also a test, so it
runs on every `pytest`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "docs/architecture.md")


def live_facts() -> dict:
    """Everything the documents are allowed to assert, read from real output."""
    recon = json.loads((ROOT / "outputs" / "recon.json").read_text())
    det = json.loads((ROOT / "outputs" / "benchmark.json").read_text())
    counts, pos = recon["counts"], recon["position"]
    facts = {
        "records": counts["total_records"],
        "invoices": counts["erp_invoices"],
        "gateway_rows": counts["pg_transactions"],
        "bank_lines": counts["bank_lines"],
        "captured": pos["captured"],
        "settled_net": pos["settled_net"],
        "landed": pos["landed_in_bank"],
        "in_transit": pos["in_transit"],
        "benchmark_records": det["total_records"],
        "benchmark_seeds": det["seeds"],
        "det_link_f1": f"{det['summary']['link_f1']['mean']:.4f}",
        "det_exc_f1": f"{det['summary']['exc_f1']['mean']:.4f}",
    }
    adv = ROOT / "outputs" / "adversarial.json"
    if adv.exists():
        a = json.loads(adv.read_text())
        facts["adv_false_links"] = a["false_links"]
        facts["adv_invented"] = f"{a['invented_cause_rate']:.0%}"
    return facts


def test_count() -> int:
    out = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q",
                          "--collect-only"], capture_output=True, text=True).stdout
    return len(re.findall(r"::", out))


def grouped(value) -> str:
    """The Indian grouping the documents print amounts in."""
    whole, _, frac = str(value).partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{whole}.{frac}" if frac else whole


def check() -> list[str]:
    facts = live_facts()
    n_tests = test_count()
    problems: list[str] = []
    text = {f: (ROOT / f).read_text() for f in DOCS}

    def must_appear(label: str, needle: str, where=DOCS) -> None:
        if not any(needle in text[f] for f in where):
            problems.append(f"{label}: no document states {needle!r}")

    def must_not_appear(label: str, needle: str) -> None:
        for f in DOCS:
            if needle in text[f]:
                problems.append(f"{label}: {f} still says {needle!r}")

    # Record counts, as written in prose.
    must_appear("records", f"**{facts['records']}** per run")
    must_appear("composition",
                f"{facts['invoices']} invoices, {facts['gateway_rows']} gateway rows, "
                f"{facts['bank_lines']} bank lines")
    must_appear("benchmark size", f"{facts['benchmark_records']:,}")
    must_appear("test count", f"{n_tests} tests")

    # Cash bridge figures.
    for key in ("captured", "settled_net", "landed", "in_transit"):
        must_appear(f"bridge {key}", grouped(facts[key]))

    # Deterministic benchmark means.
    must_appear("deterministic link F1", facts["det_link_f1"])
    must_appear("deterministic exception F1", facts["det_exc_f1"])

    if "adv_false_links" in facts:
        must_appear("adversarial invented-cause rate", facts["adv_invented"])

    # Figures that were true once and must not linger.
    for stale in ("345 records", "117 tests", "125 tests", "132 tests", "159 tests",
                  "167 tests", "7,26,213.94", "6,51,249.38", "5,79,299.00"):
        must_not_appear("stale figure", stale)
    return problems


def main() -> int:
    problems = check()
    if not problems:
        facts = live_facts()
        print(f"documentation agrees with the code — {len(facts)} figures checked, "
              f"{test_count()} tests")
        return 0
    print(f"{len(problems)} disagreement(s) between the documents and the code:\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
