"""Turning messy source text into things that can be compared.

Nearly all of the difficulty in bank reconciliation is here rather than in the
matching algebra. A UTR is a stable 16-character identifier, but by the time it
reaches a statement export it has been lower-cased, wrapped in slashes, glued to
a counterparty name, or padded inside a template the bank changed last quarter.
Everything in this module is deterministic and unit-tested; no model is asked to
read a narration.
"""

from __future__ import annotations

import re

#: A UTR as issued: 4–5 alphanumeric bank prefix then 10–14 digits.
UTR_RE = re.compile(r"\b([A-Za-z]{4}[A-Za-z0-9]\d{10,14})\b")
#: Fallback: a long digit run that could be a reference number.
LONG_DIGITS_RE = re.compile(r"\b(\d{10,22})\b")

_RAZORPAY_HINTS = ("razorpay", "razorpaysoft", "rzp", "settlement", "settl")


def extract_utrs(narration: str) -> list[str]:
    """Every UTR-shaped token in a narration, upper-cased, in order of appearance.

    Returns a list rather than one value because split-settlement narrations and
    some bank templates carry two.
    """
    seen: list[str] = []
    for m in UTR_RE.finditer(narration or ""):
        tok = m.group(1).upper()
        if tok not in seen:
            seen.append(tok)
    return seen


def reference_numbers(text: str) -> list[str]:
    """Long digit runs, used only as a weak secondary signal."""
    return [m.group(1) for m in LONG_DIGITS_RE.finditer(text or "")]


def looks_like_settlement(narration: str) -> bool:
    """Does this line even claim to be gateway money?

    Used to keep ordinary business traffic out of the settlement candidate pool,
    which is what stops a salary debit from being 'matched' to a batch.
    """
    low = (narration or "").lower()
    return any(h in low for h in _RAZORPAY_HINTS)


def norm_id(value: str | None) -> str:
    """Case- and punctuation-insensitive form of an identifier."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def norm_name(value: str | None) -> str:
    """Counterparty name reduced to comparable tokens."""
    if not value:
        return ""
    low = re.sub(r"[^a-z ]", " ", value.lower())
    drop = {"pvt", "private", "ltd", "limited", "llp", "co", "company", "the", "and"}
    return " ".join(t for t in low.split() if t and t not in drop)


def token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of word tokens. Cheap, explainable, no model."""
    ta, tb = set(norm_name(a).split()), set(norm_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
