"""Settlement Q&A over a completed reconciliation.

Grounded, not generative: the answer is assembled from the run's own matches,
findings and position, and the model is only asked to put those facts into a
sentence. If nothing in the run is relevant to the question, it says so instead
of producing a plausible number — which in a finance tool is the only
acceptable failure mode.
"""

from __future__ import annotations

import re

from .match import ReconResult
from .money import fmt
from .position import Position
from .schema import EXCEPTION_MEANING

_ID_RE = re.compile(r"\b((?:pay|rfnd|adjs|setl)_[A-Za-z0-9]+|INV-[\w-]+|bank:\d+|"
                    r"[A-Z]{4}[A-Z0-9]\d{10,14})\b", re.I)
_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "is", "are", "was", "why",
         "what", "which", "how", "much", "did", "not", "and", "any", "there", "this",
         "that", "with", "does", "do", "we", "our", "my", "it", "has", "have"}


def _stem(w: str) -> str:
    """Crudest possible stemmer: drop a trailing plural s. Enough to let
    'settlements' in a question reach a record that says 'settlement'."""
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def _tokens(q: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", q.lower())
            if w not in _STOP and len(w) > 2}


def _haystack(*parts: str) -> set[str]:
    """Word tokens, not raw text.

    Substring matching looked equivalent and was not: `"bank" in hay` is true of
    `method=netbanking`, so a card fee variance outranked the settlements that
    had genuinely not reached the bank. Underscores split, so MISSING_IN_BANK
    still contributes 'missing', 'in' and 'bank'.
    """
    text = " ".join(parts).lower().replace("_", " ")
    return {_stem(w) for w in re.findall(r"[a-z]+", text)}


def retrieve(question: str, res: ReconResult, pos: Position, k: int = 6) -> list[str]:
    """Pull the facts that bear on the question. Pure lookup, no model."""
    ids = {m.group(1).lower() for m in _ID_RE.finditer(question)}
    toks = _tokens(question)
    scored: list[tuple[int, int, str]] = []

    for f in res.findings:
        raw = (f"{f.key} {f.code.value} {f.proposed_action} "
               f"{' '.join(str(v) for v in f.evidence.values())}").lower()
        # The code's own definition is part of what the record is *about*.
        words = _haystack(raw, EXCEPTION_MEANING.get(f.code, ""))
        score = 6 * sum(1 for i in ids if i in raw) + sum(1 for t in toks if t in words)
        if score:
            ev = "; ".join(f"{k2}={v}" for k2, v in list(f.evidence.items())[:5])
            scored.append((score, abs(f.value_at_risk_paise),
                           f"[{f.code.value}] {f.key} — "
                           f"exposure {fmt(f.value_at_risk_paise)}. {ev}. "
                           f"Action: {f.proposed_action}"))
    for m in res.matches:
        raw = f"{m.left} {' '.join(m.right)} {m.evidence}".lower()
        words = _haystack(raw)
        score = 6 * sum(1 for i in ids if i in raw) + sum(1 for t in toks if t in words)
        if score:
            scored.append((score, abs(m.delta_paise),
                           f"[MATCHED {m.tier.value}] {m.left} → "
                           f"{', '.join(m.right)} (confidence {m.confidence:.2f}); "
                           f"{m.evidence}"))
    # Definitions are context, not evidence, so they fill leftover slots rather
    # than competing for the top ones. Scoring them a flat 2 let them outrank a
    # real MISSING_IN_BANK record that had matched on a single token, and
    # "which settlements have not reached the bank?" answered with a glossary.
    definitions = [f"[DEFINITION] {code.value}: {meaning}"
                   for code, meaning in EXCEPTION_MEANING.items()
                   if toks & _haystack(code.value, meaning)]

    # Equal relevance is broken by money at stake. Lexical scoring cannot tell
    # "not reached the bank" from "arrived in two parts" — both are settlement
    # and bank words — but a controller always wants the ₹44,994 that never
    # landed above a split credit that reconciled to zero.
    scored.sort(key=lambda s: (-s[0], -s[1]))
    facts = [t for _, _, t in scored[:k]]
    facts += definitions[:max(0, k - len(facts))]
    if toks & {"cash", "position", "bank", "transit", "settled", "balance", "money"}:
        facts.append(
            f"[POSITION] net settled {fmt(pos.settled_net)}, landed in bank "
            f"{fmt(pos.landed_in_bank)}, still in transit {fmt(pos.in_transit)}, "
            f"open receivables {fmt(pos.open_receivables)}, unexplained bank credits "
            f"{fmt(pos.unexplained_credits)}.")
    return facts


_SYSTEM = (
    "You are a finance controller answering a question about a reconciliation that has "
    "already been run. Answer only from the facts listed. Quote record ids and amounts "
    "exactly as they appear. Never add, subtract or otherwise compute a new number — if "
    "a total is not already in the facts, say it is not available rather than working it "
    "out. Amounts are Indian rupees: write INR or nothing, never a dollar sign. "
    "If the facts do not answer the question, say so plainly. "
    "At most three sentences. No lists, no headings, no preamble.")

#: Aggregation the model is not permitted to perform, and a currency it must not use.
_BANNED = re.compile(r"\$|\b(total(?:l?ing|s)?|combined|sum of|altogether|adds? up)\b",
                     re.I)

#: Amount-shaped tokens: 1,23,456.78 / 8033.51. Years and record ids do not match.
_AMOUNT_RE = re.compile(r"\d[\d,]*\.\d{2}")


def _amounts(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _AMOUNT_RE.finditer(text)}


def check_numbers(reply: str, facts: list[str]) -> set[str]:
    """Every amount in the answer must already appear in the facts.

    A 1.5B model asked about cash in transit will helpfully total four on-hold
    balances and present the sum as if it were a reported figure. It is not —
    on-hold funds are excluded *before* batching, so that total is both invented
    and wrong. Rather than trusting a prompt to prevent it, this checks: any
    amount the model produced that is not in its own evidence is a fabrication.
    """
    return _amounts(reply) - _amounts(" ".join(facts))


def answer(question: str, res: ReconResult, pos: Position, model=None) -> dict:
    facts = retrieve(question, res, pos)
    if not facts:
        return {"question": question, "answer":
                "Nothing in this reconciliation run bears on that question.",
                "facts": [], "grounded": True, "model_used": False}
    if model is None:
        return {"question": question,
                "answer": "Relevant records from this run:\n  " + "\n  ".join(facts),
                "facts": facts, "grounded": True, "model_used": False}
    body = "Question: " + question + "\n\nFacts from the run:\n" + "\n".join(
        f"- {f}" for f in facts)
    text = " ".join(model._generate(_SYSTEM, body).split())
    invented = check_numbers(text, facts)
    banned = _BANNED.search(text)
    if invented or banned:
        why = []
        if invented:
            why.append("amounts that are not in the underlying records "
                       f"({', '.join(sorted(invented))})")
        if banned:
            why.append(f"an aggregation it was not permitted to compute "
                       f"({banned.group(0)!r})")
        return {"question": question,
                "answer": ("The model produced " + " and ".join(why) +
                           ", so its wording is withheld. The relevant records are:\n  "
                           + "\n  ".join(facts)),
                "facts": facts, "grounded": True, "model_used": True,
                "numeric_check": "failed", "invented_amounts": sorted(invented),
                "rejected_phrase": banned.group(0) if banned else None}
    return {"question": question, "answer": text, "facts": facts,
            "grounded": True, "model_used": True, "numeric_check": "passed"}
