"""How often does the model turn an instruction into the right rule?

The compiler is the model's most valuable job here and its least reliable one.
Reporting that it works because it worked once would be the same mistake this
project keeps trying not to make, so this puts a number on it: a set of
instructions, the rule each one should produce, and a count.

The number is not the point on its own. What matters is that a wrong
compilation cannot reach a close — every rule is shown to the person who asked
for it and backtested against known books before it can be enabled — so the
compiler's accuracy sets how much typing a controller saves, not how much risk
they take.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.rules import RuleError, catalogue, parse_compiled       # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs"

#: Instruction, and the set of (field, op) pairs a correct reading produces.
#: Thresholds are checked separately; getting the *shape* right is the hard part.
CASES = [
    ("link a credit if the narration mentions ACME and it is within 2 percent of the invoice",
     {("narration", "contains"), ("amount_gap_pct", "at_most")}),
    ("only link credits over 50000 rupees",
     {("credit_amount", "at_least")}),
    ("link a credit that landed within 10 days of the invoice",
     {("days_after_invoice", "at_most")}),
    ("if the narration mentions the customer's own name, link it",
     {("narration_mentions_customer", "is_true")}),
    ("link credits whose narration does not mention REVERSAL and are within 1 percent",
     {("narration", "not_contains"), ("amount_gap_pct", "at_most")}),
    ("link a credit if the narration contains CHENNAI and the invoice is under 20000 rupees",
     {("narration", "contains"), ("invoice_amount", "at_most")}),
    ("link anything within half a percent of an invoice raised in the last 90 days",
     {("amount_gap_pct", "at_most"), ("days_after_invoice", "at_most")}),
    ("if the narration mentions the customer and the gap is at most 3 percent, link it",
     {("narration_mentions_customer", "is_true"), ("amount_gap_pct", "at_most")}),
]


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from kosh.cli import RULE_EXAMPLES
    from kosh.llm import LocalAdjudicator

    adj = LocalAdjudicator()
    print(f"loading {adj.name} …", flush=True)
    print(f"loaded in {adj.load():.1f}s on {adj.device}\n", flush=True)

    rows, exact, valid = [], 0, 0
    for instruction, want in CASES:
        raw = adj.compile_rule(instruction, catalogue(), RULE_EXAMPLES)
        try:
            rule = parse_compiled(raw, author="eval", source_text=instruction)
            got = {(c.field, c.op) for c in rule.when}
            valid += 1
            ok = got == want
            exact += ok
            note = "" if ok else f"read as {sorted(got)}"
        except RuleError as exc:
            got, ok, note = set(), False, f"refused: {exc}"[:90]
        rows.append({"instruction": instruction, "expected": sorted(want),
                     "got": sorted(got), "exact": ok, "note": note})
        print(f"  {'OK ' if ok else 'no '} {instruction[:66]}")
        if note:
            print(f"        {note}")

    n = len(CASES)
    print(f"\n  compiled to a valid rule   {valid}/{n}")
    print(f"  compiled to the right rule {exact}/{n}  ({exact / n:.0%})")
    print("\n  Every one of these is shown to the person who asked and backtested")
    print("  before it can be enabled, so a wrong reading costs a retype, not money.")

    OUT.mkdir(exist_ok=True)
    (OUT / "rule_compile_eval.json").write_text(json.dumps(
        {"model": adj.name, "cases": n, "valid": valid, "exact": exact,
         "accuracy": round(exact / n, 4), "rows": rows}, indent=2))
    print(f"\nwrote {OUT / 'rule_compile_eval.json'}")


if __name__ == "__main__":
    main()
