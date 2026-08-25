"""The local model layer. Free, offline, and deliberately boxed in.

Two jobs, and only two:

  1. *Adjudicate* a residual the deterministic tiers could not settle. The
     candidates are fixed before the model is called; the model returns one of
     them or NONE. It cannot name a record that was not offered, and every
     answer it gives is re-checked against arithmetic before it is accepted.
  2. *Narrate* an exception for the controller reading the report.

Neither job lets the model touch a number that ends up in the books. That is
the whole point: a reconciliation is an audited artefact, and 'the model said
so' is not an audit trail. What the model contributes is the thing arithmetic
genuinely cannot do — reading a mangled counterparty name in a bank narration.

Runs on Qwen2.5-1.5B-Instruct from the local Hugging Face cache with
HF_HUB_OFFLINE=1. No API key, no network, no per-token cost.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

GENERATOR = "Qwen/Qwen2.5-1.5B-Instruct"
#: Pinned commit. Two reasons, one of them practical: a reconciliation that a
#: model touched must be reproducible against the exact weights that produced
#: it, and a bare local cache often has no `refs/main` to resolve "main"
#: against, so an unpinned offline load fails outright.
REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
_CHOICE_RE = re.compile(r"CHOICE\s*[:\-]\s*(NONE|[A-Z])\b", re.I)
_REASON_RE = re.compile(r"REASON\s*[:\-]\s*(.+)", re.I)
_LABELS = "ABCDEFGH"


@dataclass
class Adjudication:
    choice: str | None
    confidence: float
    reason: str

    def as_dict(self) -> dict:
        return {"choice": self.choice, "confidence": self.confidence, "reason": self.reason}


class StubAdjudicator:
    """Model-free stand-in: always declines to adjudicate.

    Used by the test suite and by `--llm off`, so that every deterministic
    number in the report can be reproduced on a machine with no model at all.
    Declining is the honest default — a stub that guessed would inflate the
    measured accuracy of the tier it is standing in for.
    """

    name = "stub (no model)"
    calls: int = 0

    def choose(self, question: str, candidates: list[dict]) -> Adjudication:
        self.calls += 1
        return Adjudication(None, 0.0, "no model loaded; residual left for a human")

    # The engine calls these; both funnel into `choose`.
    def choose_payment(self, invoice, candidates) -> dict:
        return self.choose("", []).as_dict()

    def choose_bank_line(self, batch, candidates) -> dict:
        return self.choose("", []).as_dict()

    def choose_invoice(self, line, candidates) -> dict:
        return self.choose("", []).as_dict()

    def narrate(self, finding, context: str = "") -> str:
        return ""

    def read_narration(self, line, candidates) -> dict:
        return self.choose("", []).as_dict()

    def propose_cause(self, finding: dict) -> str:
        return ""


@dataclass
class LocalAdjudicator:
    """Qwen2.5-1.5B-Instruct, loaded once, from the local cache."""

    device: str = "auto"
    offline: bool = True
    max_new_tokens: int = 96
    name: str = GENERATOR
    calls: int = 0
    seconds: float = 0.0
    _tok: object = field(default=None, repr=False)
    _model: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    def load(self) -> float:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        t0 = time.perf_counter()
        if self.device == "auto":
            self.device = ("mps" if torch.backends.mps.is_available()
                           else "cuda" if torch.cuda.is_available() else "cpu")
        self._tok = AutoTokenizer.from_pretrained(
            GENERATOR, revision=REVISION, local_files_only=self.offline)
        self._model = AutoModelForCausalLM.from_pretrained(
            GENERATOR, revision=REVISION, local_files_only=self.offline,
            dtype=torch.float16 if self.device != "cpu" else torch.float32).to(self.device)
        self._model.eval()
        return time.perf_counter() - t0

    def _generate(self, system: str, user: str) -> str:
        import torch
        if self._model is None:
            self.load()
        text = self._tok.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tokenize=False, add_generation_prompt=True)
        ids = self._tok([text], return_tensors="pt").to(self.device)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = self._model.generate(**ids, max_new_tokens=self.max_new_tokens,
                                       do_sample=False, temperature=None, top_p=None,
                                       top_k=None,
                                       pad_token_id=self._tok.eos_token_id)
        self.seconds += time.perf_counter() - t0
        self.calls += 1
        return self._tok.decode(out[0][ids["input_ids"].shape[1]:],
                                skip_special_tokens=True).strip()

    # ---------------------------------------------------------------- choosing

    _SYSTEM = (
        "You are a finance controller reconciling a bank statement. "
        "You will be shown one unexplained item and a short list of labelled candidates. "
        "Pick the single candidate that is the same underlying transaction, or NONE if "
        "none of them is. Judge on the counterparty name and the amount relationship "
        "only. Never invent a candidate. "
        "Reply in exactly two lines:\nCHOICE: <letter or NONE>\nREASON: <one sentence>")

    def choose(self, question: str, candidates: list[dict]) -> Adjudication:
        if not candidates:
            return Adjudication(None, 0.0, "no candidates offered")
        lines = [f"{_LABELS[i]}. " + "; ".join(f"{k}={v}" for k, v in c.items() if k != "key")
                 for i, c in enumerate(candidates[:len(_LABELS)])]
        raw = self._generate(self._SYSTEM, f"{question}\n\nCandidates:\n" + "\n".join(lines))
        m = _CHOICE_RE.search(raw)
        reason = (_REASON_RE.search(raw).group(1).strip()[:200]
                  if _REASON_RE.search(raw) else raw.replace("\n", " ")[:200])
        if not m or m.group(1).upper() == "NONE":
            return Adjudication(None, 0.0, reason or "model declined")
        idx = _LABELS.find(m.group(1).upper())
        if idx < 0 or idx >= len(candidates):
            return Adjudication(None, 0.0, f"model named an out-of-range option: {raw[:80]!r}")
        return Adjudication(candidates[idx]["key"], 0.62, reason)

    def choose_invoice(self, line, candidates: list[dict]) -> dict:
        q = (f"Unexplained bank credit of INR {line['amount']} on {line['value_date']}, "
             f"narration: \"{line['narration']}\".\n"
             "Which unpaid invoice does this credit settle?")
        return self.choose(q, candidates).as_dict()

    def choose_payment(self, invoice, candidates: list[dict]) -> dict:
        q = (f"Unpaid invoice {invoice['invoice_no']} for INR {invoice['gross']} to "
             f"{invoice['customer']} dated {invoice['invoice_date']}.\n"
             "Which gateway payment covers it?")
        return self.choose(q, candidates).as_dict()

    def read_narration(self, line: dict, candidates: list[dict]) -> dict:
        """A bank line that claims to be gateway money but whose reference the
        extractor could not parse.

        This is the one thing regular expressions genuinely cannot do: absorb a
        statement format nobody has written a pattern for. The model is still
        choosing from a fixed list of open batches, and the amount is checked
        afterwards, so a wrong read cannot post a wrong number.
        """
        q = (f"Bank credit of INR {line['amount']} on {line['value_date']}. The "
             f"statement line reads: \"{line['narration']}\".\n"
             "Which settlement batch is this credit paying?")
        return self.choose(q, candidates).as_dict()

    def choose_bank_line(self, batch, candidates: list[dict]) -> dict:
        q = (f"Settlement batch {batch['settlement_id']} netting INR {batch['net']} on "
             f"{batch['settled_at']}.\nWhich bank credit is this batch?")
        return self.choose(q, candidates).as_dict()

    # --------------------------------------------------------------- narrating

    _NARRATE = (
        "You are a finance controller writing the exception section of a month-end "
        "reconciliation pack. Explain the item to a CFO in at most two sentences: what it "
        "is, and what it means for cash. Use only the facts given. Do not invent amounts, "
        "dates or names. Do not add a greeting or a heading.")

    def narrate(self, finding: dict, context: str = "") -> str:
        facts = "\n".join(f"{k}: {v}" for k, v in finding.items())
        out = self._generate(self._NARRATE, f"{context}\n{facts}".strip())
        return " ".join(out.split())[:400]

    _CAUSE = (
        "You are a finance controller looking at a reconciliation break the software "
        "could not categorise. From the figures given, suggest in one short sentence "
        "what most likely caused it — for example a currency conversion, a bank "
        "recall, a netting, or a correspondent charge. Begin with 'Possibly'. Do not "
        "state it as fact, do not invent any number, and do not repeat the figures.")

    def propose_cause(self, finding: dict) -> str:
        """A hypothesis for a break the taxonomy has no code for.

        Deliberately advisory. It is attached alongside UNCLASSIFIED and never
        replaces it, because a guess dressed as a category is exactly what the
        UNCLASSIFIED code exists to prevent.
        """
        facts = "\n".join(f"{k}: {v}" for k, v in finding.items())
        out = " ".join(self._generate(self._CAUSE, facts).split())[:220]
        return out if out.lower().startswith("possibly") else ""
