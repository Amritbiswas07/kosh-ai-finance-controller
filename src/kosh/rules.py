"""Rules a controller writes in English, compiled into something inspectable.

Until now the model made decisions: shown a residual and some candidates, it
picked one. That is useful and tightly bounded, but it is also the least
durable thing a model can do — the judgement evaporates the moment the run
ends, and the next run asks again.

A rule is the opposite. A controller says what they know:

    "if a credit's narration mentions our Chennai branch and the amount is
     within 5% of an open invoice raised in the last 60 days, link it"

and the model's job is not to decide anything. It is to turn that sentence into
a **structure** — fields, operators, thresholds — which is then read by a person,
backtested against history, and from that point evaluated by ordinary arithmetic
forever. The model parses intent, which no regular expression can. The rule does
the matching, which no model should.

Nothing here executes text. A rule is a list of typed conditions drawn from a
fixed catalogue; a field or operator outside it is refused at compile time, so
the worst a bad compilation can produce is a rule that matches nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable

from .money import to_rupees
from .normalize import norm_name, token_overlap
from .schema import BankLine, Invoice

# --------------------------------------------------------------------- fields

#: What a rule may look at, and what it means. The catalogue is the contract:
#: it is handed to the model as the only vocabulary it may use, and it is what
#: validation checks against. Adding a field here is a deliberate act.
FIELDS: dict[str, str] = {
    "narration": "the text of the bank credit's statement line",
    "customer": "the customer named on the invoice",
    "amount_gap_pct": "how far the credit is from the invoice total, as a percentage",
    "days_after_invoice": "days between the invoice date and the credit landing",
    "credit_amount": "the amount of the bank credit, in rupees",
    "invoice_amount": "the total of the invoice, in rupees",
    "narration_mentions_customer": "whether the customer's name appears in the narration",
}

TEXT_FIELDS = {"narration", "customer"}
NUMERIC_FIELDS = {"amount_gap_pct", "days_after_invoice", "credit_amount",
                  "invoice_amount"}
BOOL_FIELDS = {"narration_mentions_customer"}

OPERATORS: dict[str, str] = {
    "contains": "the text contains this word or phrase (case-insensitive)",
    "not_contains": "the text does not contain it",
    "equals": "the text is exactly this",
    "at_most": "the number is this or less",
    "at_least": "the number is this or more",
    "is_true": "the condition holds",
    "is_false": "it does not",
}

_TEXT_OPS = {"contains", "not_contains", "equals"}
_NUM_OPS = {"at_most", "at_least"}
_BOOL_OPS = {"is_true", "is_false"}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str | float | None = None

    def validate(self) -> None:
        if self.field not in FIELDS:
            raise RuleError(
                f"{self.field!r} is not something a rule can look at. "
                f"Available: {', '.join(sorted(FIELDS))}")
        if self.op not in OPERATORS:
            raise RuleError(
                f"{self.op!r} is not an operator. "
                f"Available: {', '.join(sorted(OPERATORS))}")
        allowed = (_TEXT_OPS if self.field in TEXT_FIELDS
                   else _NUM_OPS if self.field in NUMERIC_FIELDS else _BOOL_OPS)
        if self.op not in allowed:
            raise RuleError(
                f"{self.op!r} cannot be applied to {self.field!r}; "
                f"that field takes {', '.join(sorted(allowed))}")
        if self.field in NUMERIC_FIELDS:
            try:
                float(self.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise RuleError(
                    f"{self.field!r} needs a number, got {self.value!r}") from None
        elif self.field in TEXT_FIELDS and not str(self.value or "").strip():
            raise RuleError(f"{self.field!r} needs some text to look for")

    def describe(self) -> str:
        if self.field in BOOL_FIELDS:
            return f"{FIELDS[self.field]} {'holds' if self.op == 'is_true' else 'does not hold'}"
        return f"{FIELDS[self.field]} {self.op.replace('_', ' ')} {self.value!r}"


@dataclass
class Rule:
    """A policy, not a decision. Readable, testable, revocable."""

    name: str
    when: list[Condition]
    author: str = ""
    source_text: str = ""
    enabled: bool = False
    note: str = ""
    backtest: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", self.name):
            raise RuleError(
                f"{self.name!r} is not a usable name; use lower case, digits, "
                "hyphens and underscores")
        if not self.when:
            raise RuleError("a rule with no conditions would match everything")
        if len(self.when) > 6:
            raise RuleError("more than six conditions is not a rule, it is a query")
        for c in self.when:
            c.validate()

    def describe(self) -> str:
        lines = [f"{self.name}:"]
        for c in self.when:
            lines.append(f"    and {c.describe()}")
        lines[1] = lines[1].replace("    and ", "    when ", 1)
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {**asdict(self), "when": [asdict(c) for c in self.when]}

    @classmethod
    def from_json(cls, raw: dict) -> "Rule":
        try:
            conditions = [Condition(**c) for c in raw["when"]]
        except (KeyError, TypeError) as exc:
            raise RuleError(f"malformed rule: {exc}") from None
        rule = cls(name=str(raw.get("name", "")).strip().lower(),
                   when=conditions, author=raw.get("author", ""),
                   source_text=raw.get("source_text", ""),
                   enabled=bool(raw.get("enabled", False)),
                   note=raw.get("note", ""),
                   backtest=raw.get("backtest") or {})
        rule.validate()
        return rule


# ----------------------------------------------------------------- evaluation

def _values(inv: Invoice, line: BankLine) -> dict:
    """Everything a rule may read about one candidate pair. Computed once."""
    gross = inv.gross_paise or 1
    return {
        "narration": line.narration or "",
        "customer": inv.customer or "",
        "amount_gap_pct": abs(line.amount_paise - inv.gross_paise) * 100.0 / abs(gross),
        "days_after_invoice": (line.value_date - inv.invoice_date).days,
        "credit_amount": float(to_rupees(line.amount_paise)),
        "invoice_amount": float(to_rupees(inv.gross_paise)),
        "narration_mentions_customer": token_overlap(inv.customer, line.narration) > 0,
    }


_APPLY: dict[str, Callable] = {
    "contains": lambda got, want: str(want).lower() in str(got).lower(),
    "not_contains": lambda got, want: str(want).lower() not in str(got).lower(),
    "equals": lambda got, want: str(got).strip().lower() == str(want).strip().lower(),
    "at_most": lambda got, want: float(got) <= float(want),
    "at_least": lambda got, want: float(got) >= float(want),
    "is_true": lambda got, _want: bool(got),
    "is_false": lambda got, _want: not bool(got),
}


def matches(rule: Rule, inv: Invoice, line: BankLine) -> bool:
    """Every condition must hold. No text is executed; each operator is a
    function chosen from a fixed table by name."""
    values = _values(inv, line)
    return all(_APPLY[c.op](values[c.field], c.value) for c in rule.when)


def explain(rule: Rule, inv: Invoice, line: BankLine) -> list[str]:
    """Why this pair did or did not satisfy the rule, condition by condition."""
    values = _values(inv, line)
    out = []
    for c in rule.when:
        got = values[c.field]
        ok = _APPLY[c.op](got, c.value)
        shown = f"{got:.2f}" if isinstance(got, float) else str(got)[:60]
        out.append(f"{'✓' if ok else '✗'} {c.field} {c.op} {c.value!r} (saw {shown})")
    return out


# ------------------------------------------------------------------ backtest

@dataclass
class Backtest:
    """What the rule would have done to books whose answers are known."""

    proposed: list[tuple[str, str]]
    correct: list[tuple[str, str]]
    wrong: list[tuple[str, str]]
    candidates: int

    @property
    def precision(self) -> float:
        return len(self.correct) / len(self.proposed) if self.proposed else 0.0

    def to_json(self) -> dict:
        return {"proposed": len(self.proposed), "correct": len(self.correct),
                "wrong": len(self.wrong), "candidates_considered": self.candidates,
                "precision": round(self.precision, 4),
                "wrong_pairs": [list(p) for p in self.wrong[:5]]}

    def verdict(self, floor: float = 1.0) -> str:
        if not self.proposed:
            return "matches nothing in the history — it would never fire"
        if self.precision < floor:
            return (f"links {len(self.wrong)} pair(s) that are not real, "
                    f"precision {self.precision:.0%}")
        return f"links {len(self.correct)} pair(s), all of them correct"


def backtest(rule: Rule, invoices, lines, truth: dict[str, str]) -> Backtest:
    """Run the rule over history before anyone lets it near a live close.

    `truth` maps an invoice number to the bank line that really settled it. A
    rule is only worth enabling if every link it proposes is one of those — a
    rule that is usually right is a rule that quietly mislinks money.
    """
    proposed, correct, wrong = [], [], []
    considered = 0
    for inv in invoices:
        for line in lines:
            considered += 1
            if not matches(rule, inv, line):
                continue
            pair = (inv.invoice_no, line.key)
            proposed.append(pair)
            (correct if truth.get(inv.invoice_no) == line.key else wrong).append(pair)
    return Backtest(proposed, correct, wrong, considered)


def catalogue() -> str:
    """The vocabulary handed to the model, and nothing beyond it."""
    fields = "\n".join(f"  {k} — {v}" for k, v in FIELDS.items())
    ops = "\n".join(f"  {k} — {v}" for k, v in OPERATORS.items())
    return (f"FIELDS you may use:\n{fields}\n\nOPERATORS you may use:\n{ops}\n\n"
            "Text fields take contains, not_contains, equals. Number fields take "
            "at_most, at_least. The yes/no field takes is_true or is_false.")


def parse_compiled(raw: str, *, author: str = "", source_text: str = "") -> Rule:
    """Read what the model returned, and refuse anything outside the catalogue."""
    block = re.search(r"\{.*\}", raw, re.S)
    if not block:
        raise RuleError("the model did not return a rule")
    try:
        data = json.loads(block.group(0))
    except json.JSONDecodeError as exc:
        raise RuleError(f"the model's rule is not valid JSON: {exc}") from None
    data.setdefault("author", author)
    data.setdefault("source_text", source_text)
    return Rule.from_json(data)
