"""The reconciliation engine: a deterministic cascade over three legs.

Design commitment: **the model never decides whether two records match.**
Matching is arithmetic and identifier logic, and it is the part a controller
will be audited on, so it stays deterministic, ordered and explainable. Each
tier is strictly more permissive than the last and runs only on what the tier
above could not resolve, so the cheap exact paths absorb the bulk of the volume
and the expensive paths see a handful of rows.

    T0  exact identifier          order_id / UTR present on both sides
    T1  normalised identifier     case, punctuation and prefix differences
    T2  amount + date window      globally optimal 1:1 assignment (Hungarian)
    T3  aggregate                 1:N split credits, N:1 consolidated payouts
    T4  adjudicated               a local model breaks a genuine tie  (opt-in)

Only T4 involves a model, it only ever *chooses among candidates the
deterministic tiers already produced*, and it can only ever return one of those
candidates or 'no match'. It cannot invent a counterparty or an amount.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from itertools import combinations

import numpy as np
from scipy.optimize import linear_sum_assignment

from .money import to_rupees
from .normalize import extract_utrs, looks_like_settlement, norm_id, token_overlap
from .schema import (FEE_GST_BPS, MDR_BPS, BankLine, Dataset, ExceptionCode,
                     Invoice, PGTxn, SettlementBatch)

# Tolerances, stated once, in paise. A controller should be able to read these
# and disagree with them; that is the point of not burying them in the code.
AMOUNT_TOL = 100            # ₹1.00 — below this, treat two amounts as equal
FEE_TOL = 100               # ₹1.00 — MDR rounding differs across gateways
TAX_TOL = 100               # ₹1.00
DATE_WINDOW_ERP = 5         # days an invoice may lead or lag its payment
DATE_WINDOW_BANK = 4        # days a settlement may take to hit the account
MAX_MERGE_SIZE = 3          # batches we will consider inside one consolidated credit
NAME_TOL = 0.34             # token overlap below which a narration name is not proof
DIRECT_WINDOW = 45          # days a direct bank payment may lag its invoice
TDS_BPS = (100, 200, 1000)  # 1% / 2% u/s 194C, 10% u/s 194J
ADJUDICATION_TOL = 5000     # ₹50 — the widest gap a model-proposed match may carry
#: A settlement delta this small is consistent with a correspondent bank charge.
#: Anything larger is not something the engine can account for, and says so.
CHARGE_TOL_PAISE = 10_000   # ₹100
CHARGE_TOL_FRAC = 0.0025    # or 0.25% of the batch, whichever is larger


class Tier(str, Enum):
    CONFIRMED = "T0_HUMAN_CONFIRMED"     # a person decided; nothing outranks that
    EXACT_ID = "T0_EXACT_ID"
    NORMALIZED_ID = "T1_NORMALIZED_ID"
    AMOUNT_DATE = "T2_AMOUNT_DATE"
    AGGREGATE = "T3_AGGREGATE"
    ADJUDICATED = "T4_ADJUDICATED"


class Leg(str, Enum):
    ERP_PG = "erp_to_gateway"
    INVOICE_BANK = "invoice_to_bank"
    PG_INTEGRITY = "gateway_integrity"
    BATCH_BANK = "settlement_to_bank"


#: Which legs may consult the model, by default. Legs A and C are excluded on
#: evidence, not on principle: their residuals are records with no counterparty
#: in the data at all, so there is nothing for a model to find and every answer
#: it gives is a false positive. See `scripts/ablation.py`.
DEFAULT_ADJUDICATED_LEGS = frozenset({Leg.INVOICE_BANK, Leg.BATCH_BANK})
ALL_ADJUDICATED_LEGS = frozenset(Leg)


class Disposition(str, Enum):
    AUTO_RESOLVED = "auto_resolved"   # engine explained it; no human needed
    NEEDS_REVIEW = "needs_review"     # a person must decide or chase


@dataclass
class Match:
    leg: Leg
    left: str
    right: tuple[str, ...]
    tier: Tier
    confidence: float
    delta_paise: int
    evidence: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"leg": self.leg.value, "left": self.left, "right": list(self.right),
                "tier": self.tier.value, "confidence": round(self.confidence, 3),
                "delta": str(to_rupees(self.delta_paise)), "evidence": self.evidence}


@dataclass
class Finding:
    key: str
    source: str
    code: ExceptionCode
    disposition: Disposition
    value_at_risk_paise: int
    evidence: dict = field(default_factory=dict)
    proposed_action: str = ""
    narrative: str = ""

    def to_json(self) -> dict:
        return {"key": self.key, "source": self.source, "code": self.code.value,
                "disposition": self.disposition.value,
                "value_at_risk": str(to_rupees(self.value_at_risk_paise)),
                "evidence": self.evidence, "proposed_action": self.proposed_action,
                "narrative": self.narrative}


@dataclass
class ReconResult:
    matches: list[Match] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    confirmed_keys: set = field(default_factory=set)
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    adjudications: list[dict] = field(default_factory=list)

    def by_code(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.code.value] = out.get(f.code.value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def unresolved(self) -> list[Finding]:
        return [f for f in self.findings if f.disposition is Disposition.NEEDS_REVIEW]


def _days(a: date, b: date) -> int:
    return abs((a - b).days)


def expected_fee(amount_paise: int, method: str) -> tuple[int, int]:
    fee = round(amount_paise * MDR_BPS.get(method, 200) / 10_000)
    return fee, round(fee * FEE_GST_BPS / 10_000)


# --------------------------------------------------------------------------- #
#  Leg A — ERP invoice ↔ gateway payment
# --------------------------------------------------------------------------- #

def _apply_confirmed(ds: Dataset, batches, confirmed, res: ReconResult) -> None:
    """Replay a person's decisions before any tier runs.

    Once a controller has said *this credit is that payout*, asking again every
    morning is not diligence, it is the tool forgetting. A confirmed link is
    recorded at the top tier and its records are withheld from the cascade, so
    the same question is never put twice.
    """
    known = ({i.invoice_no for i in ds.invoices} | {t.entity_id for t in ds.pg}
             | {b.settlement_id for b in batches} | {b.key for b in ds.bank})
    for leg, left, right in sorted(confirmed):
        if left not in known or right not in known:
            continue                      # the records are not in this period
        res.matches.append(Match(
            Leg(leg), left, (right,), Tier.CONFIRMED, 1.0, 0,
            {"on": "confirmed_by_a_person", "note": "carried from an earlier run"}))
        res.confirmed_keys.update((left, right))


def reconcile_erp_to_gateway(ds: Dataset, res: ReconResult, adjudicator=None,
                             skip: "set[str] | None" = None) -> None:
    skip = skip or set()
    payments = [t for t in ds.pg if t.type == "payment" and t.entity_id not in skip]
    handled: set[str] = set()   # captures already accounted for as instalments

    # Duplicates first: a second capture on an order is not a candidate for the
    # invoice, it is an exception in its own right. Earliest capture wins.
    inv_by_order_pre = _index(ds.invoices, lambda i: i.order_id)
    inv_by_receipt_pre = _index(ds.invoices, lambda i: norm_id(i.invoice_no))

    def group_key(p: PGTxn) -> str:
        """Group captures that belong to the same sale.

        `order_id` when the export carries one — and when it does not, the
        receipt, because a gateway that omitted the order reference will still
        usually carry the invoice number. Keying on the entity id alone (the
        previous behaviour) made every id-less capture its own group, so a
        straightforward double charge came back as UNBILLED_PAYMENT.
        """
        if p.order_id:
            return f"order:{p.order_id}"
        if p.order_receipt:
            return f"receipt:{norm_id(p.order_receipt)}"
        return f"solo:{p.entity_id}"

    by_order: dict[str, list[PGTxn]] = {}
    for p in payments:
        by_order.setdefault(group_key(p), []).append(p)
    primary, duplicates = [], []
    for order, group in by_order.items():
        group.sort(key=lambda p: (p.created_at, p.entity_id))

        # Several captures that add up to the invoice are instalments, not
        # duplicates. Calling them duplicates was not merely a wrong label: the
        # proposed action said "refund it", which would take back money the
        # customer legitimately owed.
        if len(group) > 1:
            inv = (inv_by_order_pre.get(group[0].order_id or "\0")
                   or inv_by_receipt_pre.get(norm_id(group[0].order_receipt) or "\0"))
            total = sum(g.amount_paise for g in group)
            if inv and abs(total - inv.gross_paise) <= AMOUNT_TOL:
                res.matches.append(Match(
                    Leg.ERP_PG, inv.invoice_no, tuple(g.entity_id for g in group),
                    Tier.AGGREGATE, 0.97, 0,
                    {"on": "instalments_sum_to_invoice",
                     "parts": len(group),
                     "amounts": [str(to_rupees(g.amount_paise)) for g in group],
                     "invoice_gross": str(to_rupees(inv.gross_paise))}))
                res.findings.append(Finding(
                    key=inv.invoice_no, source="erp", code=ExceptionCode.PART_PAYMENT,
                    disposition=Disposition.AUTO_RESOLVED, value_at_risk_paise=0,
                    evidence={"parts": len(group),
                              "payments": [g.entity_id for g in group],
                              "dates": [g.created_at.date().isoformat() for g in group],
                              "sums_to": str(to_rupees(total)),
                              "customer": inv.customer},
                    proposed_action="No action: the instalments reconcile exactly. "
                                    "Post them against the one invoice."))
                for g in group:
                    handled.add(g.entity_id)
                continue

        primary.append(group[0])
        for extra in group[1:]:
            duplicates.append(extra)
            res.findings.append(Finding(
                key=extra.entity_id, source="pg", code=ExceptionCode.DUPLICATE_PAYMENT,
                disposition=Disposition.NEEDS_REVIEW,
                value_at_risk_paise=extra.amount_paise,
                evidence={"order_id": order, "first_capture": group[0].entity_id,
                          "first_at": group[0].created_at.isoformat(),
                          "duplicate_at": extra.created_at.isoformat(),
                          "amount": str(to_rupees(extra.amount_paise))},
                proposed_action=f"Refund {extra.entity_id} and credit-note the order, "
                                f"or confirm two genuine shipments against {order}."))

    open_inv = {i.invoice_no: i for i in ds.invoices
                if i.invoice_no not in skip
                and i.invoice_no not in {m.left for m in res.matches
                                         if m.leg is Leg.ERP_PG}}
    open_pay = {p.entity_id: p for p in primary if p.entity_id not in handled}

    # T0 — the order_id is on both sides.
    inv_by_order = _index(ds.invoices, lambda i: i.order_id)
    for p in list(open_pay.values()):
        inv = inv_by_order.get(p.order_id or "\0")
        if inv and inv.invoice_no in open_inv:
            delta = p.amount_paise - inv.gross_paise
            res.matches.append(Match(
                Leg.ERP_PG, inv.invoice_no, (p.entity_id,), Tier.EXACT_ID,
                1.0 if abs(delta) <= AMOUNT_TOL else 0.92, delta,
                {"on": "order_id", "order_id": p.order_id,
                 "invoice_gross": str(to_rupees(inv.gross_paise)),
                 "payment_amount": str(to_rupees(p.amount_paise))}))
            _flag_amount_gap(inv, p, delta, res)
            open_inv.pop(inv.invoice_no); open_pay.pop(p.entity_id)

    # T1 — the gateway carried the invoice number as the receipt.
    inv_by_norm = _index(open_inv.values(), lambda i: norm_id(i.invoice_no))
    for p in list(open_pay.values()):
        inv = inv_by_norm.get(norm_id(p.order_receipt) or "\0")
        if inv and inv.invoice_no in open_inv:
            delta = p.amount_paise - inv.gross_paise
            res.matches.append(Match(
                Leg.ERP_PG, inv.invoice_no, (p.entity_id,), Tier.NORMALIZED_ID, 0.95, delta,
                {"on": "order_receipt~invoice_no", "receipt": p.order_receipt}))
            _flag_amount_gap(inv, p, delta, res)
            open_inv.pop(inv.invoice_no); open_pay.pop(p.entity_id)

    # T2 — no shared identifier left. Assign on amount and date, globally.
    _assign_one_to_one(
        left=list(open_inv.values()), right=list(open_pay.values()),
        left_amount=lambda i: i.gross_paise, right_amount=lambda p: p.amount_paise,
        left_date=lambda i: i.invoice_date, right_date=lambda p: p.created_at.date(),
        window=DATE_WINDOW_ERP, leg=Leg.ERP_PG, res=res,
        left_key=lambda i: i.invoice_no, right_key=lambda p: p.entity_id,
        on_match=lambda i, p: (open_inv.pop(i.invoice_no, None), open_pay.pop(p.entity_id, None)),
        extra=lambda i, p: {"customer": i.customer, "method": p.method})

    # T4 — a genuine tie, handed to the model with the candidates already fixed.
    if adjudicator is not None and open_inv and open_pay:
        _adjudicate_erp(open_inv, open_pay, res, adjudicator)

    for inv in open_inv.values():
        res.findings.append(Finding(
            key=inv.invoice_no, source="erp", code=ExceptionCode.UNPAID_INVOICE,
            disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=inv.gross_paise,
            evidence={"order_id": inv.order_id, "customer": inv.customer,
                      "invoice_date": inv.invoice_date.isoformat(),
                      "gross": str(to_rupees(inv.gross_paise)),
                      "age_days": (max(i.invoice_date for i in ds.invoices) - inv.invoice_date).days},
            proposed_action=f"Chase {inv.customer} for {to_rupees(inv.gross_paise)} against "
                            f"{inv.invoice_no}, or write it off if the order was cancelled."))
    for p in open_pay.values():
        res.findings.append(Finding(
            key=p.entity_id, source="pg", code=ExceptionCode.UNBILLED_PAYMENT,
            disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=p.amount_paise,
            evidence={"order_id": p.order_id, "receipt": p.order_receipt,
                      "captured_at": p.created_at.isoformat(), "method": p.method,
                      "amount": str(to_rupees(p.amount_paise))},
            proposed_action=f"Raise an invoice for {to_rupees(p.amount_paise)} against "
                            f"{p.order_id or 'the captured order'}; revenue is currently unrecognised."))


def _index(rows, key) -> dict:
    """Index rows by an identifier, ignoring the ones that have none.

    An empty key is not an identifier. Indexing on it put every reference-less
    invoice under `""`, so a payment that also had no reference matched
    whichever of them the file happened to list last — two records that share
    nothing but an absence, linked, and the answer changing with the row order.
    """
    out = {}
    for row in rows:
        k = key(row)
        if k:
            out.setdefault(k, row)
    return out


def _flag_amount_gap(inv: Invoice, p: PGTxn, delta: int, res: ReconResult) -> None:
    """An identifier that agrees is not the same as money that agrees.

    T0 and T1 matched on an id alone, so a ₹10,000 invoice paid with ₹100 was
    reported as a clean match and never reached the exception list at all — the
    headline match rate counted a ₹9,900 hole as a win. The link is still
    correct, so the match stands; what was missing is saying that the amounts
    do not.
    """
    if abs(delta) <= AMOUNT_TOL:
        return
    rel = _tds_relation(inv.gross_paise, p.amount_paise)
    if rel and rel != "gross":
        res.findings.append(Finding(
            key=inv.invoice_no, source="erp", code=ExceptionCode.TDS_WITHHELD,
            disposition=Disposition.AUTO_RESOLVED, value_at_risk_paise=-delta,
            evidence={"relation": rel, "payment": p.entity_id,
                      "invoice_gross": str(to_rupees(inv.gross_paise)),
                      "received": str(to_rupees(p.amount_paise)),
                      "withheld": str(to_rupees(-delta)), "customer": inv.customer},
            proposed_action=f"Book {to_rupees(-delta)} as TDS receivable against "
                            f"{inv.customer} and collect Form 16A."))
        return
    short = delta < 0
    res.findings.append(Finding(
        key=inv.invoice_no, source="erp",
        code=ExceptionCode.SHORT_PAYMENT if short else ExceptionCode.OVERPAYMENT,
        disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=delta,
        evidence={"payment": p.entity_id, "matched_on": "identifier",
                  "invoice_gross": str(to_rupees(inv.gross_paise)),
                  "paid": str(to_rupees(p.amount_paise)),
                  "gap": str(to_rupees(delta)), "customer": inv.customer},
        proposed_action=(
            f"{inv.customer} paid {to_rupees(-delta)} less than {inv.invoice_no}. "
            "Chase the balance, or raise a credit note if it was agreed."
            if short else
            f"{inv.customer} paid {to_rupees(delta)} more than {inv.invoice_no}. "
            "Refund it or hold it as an advance against the next invoice.")))


def _assign_one_to_one(*, left, right, left_amount, right_amount, left_date, right_date,
                       window: int, leg: Leg, res: ReconResult, left_key, right_key,
                       on_match, extra=lambda a, b: {}) -> None:
    """Globally optimal 1:1 assignment over a feasibility-gated cost matrix.

    Greedy nearest-neighbour matching is the usual shortcut here and it is
    wrong: it lets an early row claim a partner that a later row needed, so the
    result depends on input order. The Hungarian algorithm minimises total cost
    over the whole set, which makes the output stable under row shuffling.
    """
    if not left or not right:
        return

    # Feasible pairs only. Building the full |left| x |right| matrix in a Python
    # double loop cost 1.4M comparisons at n=1200 and 20 GB of matrix at n=50k —
    # it did not degrade, it died. Since a pair is only feasible inside a narrow
    # amount tolerance, sorting the right side by amount and walking a window
    # over it finds every candidate in O(n log n + k), where k is the number of
    # pairs that could ever match.
    order = sorted(range(len(right)), key=lambda c: right_amount(right[c]))
    sorted_amounts = [right_amount(right[c]) for c in order]
    pairs: list[tuple[int, int, float]] = []
    for r, a in enumerate(left):
        amt = left_amount(a)
        lo = bisect.bisect_left(sorted_amounts, amt - AMOUNT_TOL)
        hi = bisect.bisect_right(sorted_amounts, amt + AMOUNT_TOL)
        for pos in range(lo, hi):
            c = order[pos]
            dday = _days(left_date(a), right_date(right[c]))
            if dday <= window:
                pairs.append((r, c, abs(amt - sorted_amounts[pos]) + dday * 10))
    if not pairs:
        return

    # The feasibility graph is almost always a scatter of tiny components — a
    # handful of rows that could plausibly be each other. Solving each one
    # exactly is the same answer as one big assignment, at a fraction of the
    # size, and keeps the result independent of how the file was ordered.
    for block_left, block_right in _components(pairs):
        li = {r: i for i, r in enumerate(block_left)}
        ci = {c: j for j, c in enumerate(block_right)}
        BIG = 10**9
        cost = np.full((len(block_left), len(block_right)), float(BIG))
        for r, c, w in pairs:
            if r in li and c in ci:
                cost[li[r], ci[c]] = w
        rows, cols = linear_sum_assignment(cost)
        for rr, cc in zip(rows, cols):
            if cost[rr, cc] >= BIG:
                continue
            if (int((cost[rr] == cost[rr, cc]).sum()) > 1
                    or int((cost[:, cc] == cost[rr, cc]).sum()) > 1):
                continue
            _emit_assignment(block_left[rr], block_right[cc], left, right,
                             left_amount, right_amount, left_date, right_date,
                             leg, res, left_key, right_key, on_match, extra)
    return


def _components(pairs):
    """Split the feasibility graph into independent left/right groups."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for r, c, _w in pairs:
        union(("L", r), ("R", c))
    groups: dict[tuple[str, int], tuple[list[int], list[int]]] = {}
    for node in list(parent):
        side, idx = node
        lefts, rights = groups.setdefault(find(node), ([], []))
        (lefts if side == "L" else rights).append(idx)
    return [(sorted(l), sorted(r)) for l, r in groups.values() if l and r]


def _emit_assignment(r, c, left, right, left_amount, right_amount, left_date,
                     right_date, leg, res, left_key, right_key, on_match, extra):
    a, b = left[r], right[c]
    damt = left_amount(a) - right_amount(b)
    dday = _days(left_date(a), right_date(b))
    res.matches.append(Match(
        leg, left_key(a), (right_key(b),), Tier.AMOUNT_DATE,
        round(max(0.70, 0.92 - 0.03 * dday), 3), -damt,
        {"on": "amount+date", "amount": str(to_rupees(right_amount(b))),
         "date_gap_days": dday, **extra(a, b)}))
    on_match(a, b)


def _adjudicate_erp(open_inv: dict, open_pay: dict, res: ReconResult, adjudicator) -> None:
    """Hand each stranded invoice its three nearest payments and let the model pick."""
    for inv in list(open_inv.values()):
        cands = sorted(open_pay.values(),
                       key=lambda p: (abs(p.amount_paise - inv.gross_paise),
                                      _days(inv.invoice_date, p.created_at.date())))[:3]
        if not cands:
            break
        choice = adjudicator.choose_payment(
            {"invoice_no": inv.invoice_no, "gross": str(to_rupees(inv.gross_paise)),
             "customer": inv.customer, "invoice_date": inv.invoice_date.isoformat()},
            [{"key": c.entity_id, "amount": str(to_rupees(c.amount_paise)),
              "captured": c.created_at.date().isoformat(), "method": c.method}
             for c in cands])
        res.adjudications.append({"invoice": inv.invoice_no,
                                  "candidates": [c.entity_id for c in cands],
                                  "chose": choice.get("choice"),
                                  "reason": choice.get("reason", "")})
        picked = next((c for c in cands if c.entity_id == choice.get("choice")), None)
        if picked is None or picked.entity_id not in open_pay:
            continue
        # Model proposes, arithmetic disposes. A pick whose amount cannot be
        # reconciled to the invoice is discarded however confident the model was.
        if abs(picked.amount_paise - inv.gross_paise) > ADJUDICATION_TOL:
            res.adjudications[-1]["verdict"] = "rejected_by_arithmetic"
            continue
        res.adjudications[-1]["verdict"] = "accepted"
        res.matches.append(Match(
            Leg.ERP_PG, inv.invoice_no, (picked.entity_id,), Tier.ADJUDICATED,
            float(choice.get("confidence", 0.55)), picked.amount_paise - inv.gross_paise,
            {"on": "model_adjudication", "reason": choice.get("reason", ""),
             "candidates": [c.entity_id for c in cands]}))
        open_inv.pop(inv.invoice_no); open_pay.pop(picked.entity_id)


# --------------------------------------------------------------------------- #
#  Leg B — gateway and ERP internal integrity
# --------------------------------------------------------------------------- #

def check_integrity(ds: Dataset, res: ReconResult) -> None:
    payment_ids = {t.entity_id for t in ds.pg if t.type == "payment"}

    for t in ds.pg:
        if t.type == "payment":
            if t.on_hold:
                res.findings.append(Finding(
                    key=t.entity_id, source="pg", code=ExceptionCode.FUNDS_ON_HOLD,
                    disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=t.net_paise,
                    evidence={"captured_at": t.created_at.isoformat(), "method": t.method,
                              "amount": str(to_rupees(t.amount_paise)),
                              "order_id": t.order_id},
                    proposed_action="Open a gateway ticket for the hold, and exclude this "
                                    "amount from the forward cash position until released."))
                continue
            fee, tax = expected_fee(t.amount_paise, t.method)
            drift = (t.fee_paise + t.tax_paise) - (fee + tax)
            if abs(drift) > FEE_TOL:
                res.findings.append(Finding(
                    key=t.entity_id, source="pg", code=ExceptionCode.FEE_VARIANCE,
                    disposition=Disposition.AUTO_RESOLVED, value_at_risk_paise=drift,
                    evidence={"method": t.method, "contracted_bps": MDR_BPS.get(t.method, 200),
                              "expected_fee": str(to_rupees(fee)),
                              "charged_fee": str(to_rupees(t.fee_paise)),
                              "expected_gst": str(to_rupees(tax)),
                              "charged_gst": str(to_rupees(t.tax_paise)),
                              "drift": str(to_rupees(drift))},
                    proposed_action=("Recover " if drift > 0 else "Credit back ")
                                    + f"{to_rupees(abs(drift))} of MDR against {t.entity_id}."))
        elif t.type == "refund":
            if not t.settlement_id and not t.on_hold:
                # A refund that has not been netted into a payout yet is money
                # about to leave. With a valid parent it raised nothing, and it
                # belongs to no batch, so nothing in the report mentioned it at
                # all — found by the conservation invariant, not by a case.
                res.findings.append(Finding(
                    key=t.entity_id, source="pg",
                    code=ExceptionCode.AWAITING_SETTLEMENT,
                    disposition=Disposition.NEEDS_REVIEW,
                    value_at_risk_paise=t.amount_paise,
                    evidence={"type": t.type, "amount": str(to_rupees(abs(t.amount_paise))),
                              "created_at": t.created_at.isoformat(),
                              "parent_payment_id": t.parent_payment_id},
                    proposed_action="Not yet netted into a payout. Expect it to "
                                    "reduce a forthcoming settlement; hold it out "
                                    "of the cash position until it does."))
            if t.parent_payment_id and t.parent_payment_id not in payment_ids:
                res.findings.append(Finding(
                    key=t.entity_id, source="pg", code=ExceptionCode.ORPHAN_REFUND,
                    disposition=Disposition.NEEDS_REVIEW,
                    value_at_risk_paise=abs(t.amount_paise),
                    evidence={"parent_payment_id": t.parent_payment_id,
                              "amount": str(to_rupees(abs(t.amount_paise))),
                              "created_at": t.created_at.isoformat()},
                    proposed_action=f"Locate {t.parent_payment_id} — it is outside this "
                                    "report's period or was captured on another account."))
        elif t.type == "adjustment":
            if not t.settlement_id and not t.on_hold:
                res.findings.append(Finding(
                    key=t.entity_id, source="pg",
                    code=ExceptionCode.AWAITING_SETTLEMENT,
                    disposition=Disposition.NEEDS_REVIEW,
                    value_at_risk_paise=t.amount_paise,
                    evidence={"type": t.type, "amount": str(to_rupees(abs(t.amount_paise))),
                              "created_at": t.created_at.isoformat(),
                              "dispute_id": t.dispute_id},
                    proposed_action="Not yet netted into a payout. Expect it to "
                                    "reduce a forthcoming settlement; hold it out "
                                    "of the cash position until it does."))
            res.findings.append(Finding(
                key=t.entity_id, source="pg", code=ExceptionCode.CHARGEBACK_ADJUSTMENT,
                disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=abs(t.amount_paise),
                evidence={"dispute_id": t.dispute_id,
                          "amount": str(to_rupees(abs(t.amount_paise))),
                          "created_at": t.created_at.isoformat(),
                          "settlement_id": t.settlement_id},
                proposed_action="Book the dispute debit to a chargeback expense account and "
                                "decide whether to contest before the representment window closes."))

    for inv in ds.invoices:
        due = round(inv.taxable_paise * 1800 / 10_000)
        if abs(inv.tax_paise - due) > TAX_TOL:
            res.findings.append(Finding(
                key=inv.invoice_no, source="erp", code=ExceptionCode.TAX_LINE_MISMATCH,
                disposition=Disposition.NEEDS_REVIEW,
                value_at_risk_paise=due - inv.tax_paise,
                evidence={"taxable": str(to_rupees(inv.taxable_paise)),
                          "gst_charged": str(to_rupees(inv.tax_paise)),
                          "gst_expected_18pct": str(to_rupees(due)),
                          "shortfall": str(to_rupees(due - inv.tax_paise)),
                          "customer": inv.customer},
                proposed_action=(
                    f"Reconfirm the HSN rate on {inv.invoice_no}; if 18% applies, "
                    + (f"a revised invoice for {to_rupees(due - inv.tax_paise)} of GST is due."
                       if due > inv.tax_paise else
                       f"{to_rupees(inv.tax_paise - due)} of GST was over-collected and "
                       "needs a credit note."))))


# --------------------------------------------------------------------------- #
#  Leg C — settlement batch ↔ bank credit
# --------------------------------------------------------------------------- #

def reconcile_settlement_to_bank(batches: list[SettlementBatch], ds: Dataset,
                                 res: ReconResult, adjudicator=None) -> None:
    credits = [b for b in ds.bank if b.is_credit and b.key not in res.confirmed_keys]
    open_lines = {c.key: c for c in credits}
    open_batches = {b.settlement_id: b for b in batches
                    if b.settlement_id not in res.confirmed_keys}

    # Index every UTR-shaped token found in every narration, once.
    utr_index: dict[str, list[BankLine]] = {}
    for line in credits:
        for utr in extract_utrs(line.narration):
            utr_index.setdefault(utr, []).append(line)

    # A reference that identifies two different payouts identifies neither. The
    # bank occasionally reuses one, and matching the single credit to whichever
    # batch came first in the file is a coin toss dressed as an identifier.
    batches_by_utr: dict[str, list[SettlementBatch]] = {}
    for b in batches:
        if b.utr:
            batches_by_utr.setdefault(b.utr, []).append(b)
    ambiguous_utrs = {u for u, bs in batches_by_utr.items() if len(bs) > 1}
    for utr in sorted(ambiguous_utrs):
        shared = batches_by_utr[utr]
        for b in shared:
            res.findings.append(Finding(
                key=b.settlement_id, source="settlement",
                code=ExceptionCode.AMBIGUOUS_REFERENCE,
                disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=b.net_paise,
                evidence={"utr": utr, "shared_with": [x.settlement_id for x in shared
                                                     if x is not b],
                          "net": str(to_rupees(b.net_paise)),
                          "settled_at": b.settled_at.isoformat(),
                          "bank_lines_quoting_it": [l.key for l in utr_index.get(utr, [])]},
                proposed_action=f"Reference {utr} is on {len(shared)} payouts. Ask the "
                                "gateway which credit belongs to which batch; matching "
                                "on it would be a guess."))
            open_batches.pop(b.settlement_id, None)

    # T0/T1 — the UTR is quoted in the narration.
    for batch in list(open_batches.values()):
        hits = [l for l in utr_index.get(batch.utr, []) if l.key in open_lines]
        if not hits:
            continue
        total = sum(l.amount_paise for l in hits)
        delta = total - batch.net_paise
        if len(hits) == 1 and abs(delta) <= AMOUNT_TOL:
            res.matches.append(Match(
                Leg.BATCH_BANK, batch.settlement_id, (hits[0].key,), Tier.EXACT_ID, 1.0, delta,
                {"on": "utr", "utr": batch.utr,
                 "net": str(to_rupees(batch.net_paise)), "members": len(batch.members),
                 "narration": hits[0].narration}))
        elif len(hits) > 1 and abs(delta) <= AMOUNT_TOL:
            res.matches.append(Match(
                Leg.BATCH_BANK, batch.settlement_id, tuple(l.key for l in hits),
                Tier.AGGREGATE, 0.97, delta,
                {"on": "utr+aggregate", "utr": batch.utr, "parts": len(hits),
                 "part_amounts": [str(to_rupees(l.amount_paise)) for l in hits]}))
            res.findings.append(Finding(
                key=batch.settlement_id, source="settlement",
                code=ExceptionCode.SPLIT_SETTLEMENT, disposition=Disposition.AUTO_RESOLVED,
                value_at_risk_paise=0,
                evidence={"utr": batch.utr, "parts": len(hits),
                          "lines": [l.key for l in hits],
                          "dates": [l.value_date.isoformat() for l in hits],
                          "sums_to": str(to_rupees(total))},
                proposed_action="No action: the parts reconcile exactly. Post as one receipt "
                                "so the sub-ledger keeps a single settlement line."))
        elif (len(hits) > 1
              and all(l.amount_paise == hits[0].amount_paise for l in hits)
              and abs(hits[0].amount_paise - batch.net_paise) <= AMOUNT_TOL):
            # The same payout printed more than once — a re-exported statement,
            # not extra money. Matching every copy silently doubled the cash the
            # bridge reported as landed.
            keep, copies = hits[0], hits[1:]
            res.matches.append(Match(
                Leg.BATCH_BANK, batch.settlement_id, (keep.key,), Tier.EXACT_ID, 0.95,
                keep.amount_paise - batch.net_paise,
                {"on": "utr", "utr": batch.utr, "duplicate_lines_ignored": len(copies),
                 "narration": keep.narration}))
            for c in copies:
                res.findings.append(Finding(
                    key=c.key, source="bank", code=ExceptionCode.DUPLICATE_BANK_LINE,
                    disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=c.amount_paise,
                    evidence={"utr": batch.utr, "same_as": keep.key,
                              "value_date": c.value_date.isoformat(),
                              "amount": str(to_rupees(c.amount_paise)),
                              "narration": c.narration},
                    proposed_action="This credit is identical to " + keep.key +
                                    " and carries the same reference. Confirm the "
                                    "statement was not exported twice before posting."))
        else:
            res.matches.append(Match(
                Leg.BATCH_BANK, batch.settlement_id, tuple(l.key for l in hits),
                Tier.NORMALIZED_ID, 0.80, delta,
                {"on": "utr_amount_differs", "utr": batch.utr,
                 "bank_total": str(to_rupees(total)),
                 "batch_net": str(to_rupees(batch.net_paise))}))
            res.findings.append(_classify_settlement_delta(batch, hits, total, delta))
        for l in hits:
            open_lines.pop(l.key, None)
        open_batches.pop(batch.settlement_id)

    # T3 — consolidated payouts: one credit paying several batches, no UTR quoted.
    _match_merged_payouts(open_batches, open_lines, res)

    # Reading the reference out of an unfamiliar statement format comes *before*
    # matching on amount and date. Evidence has an order — an identifier beats a
    # reference in free text, which beats an amount that happens to agree — and
    # running the blind pass first let it consume lines whose reference was
    # sitting there in plain sight.
    if adjudicator is not None:
        _recover_unreadable_narrations(open_batches, open_lines, res, adjudicator)

    # T2 — last deterministic pass: single batch, single credit, amount + date.
    _assign_one_to_one(
        left=list(open_batches.values()),
        right=[l for l in open_lines.values() if looks_like_settlement(l.narration)],
        left_amount=lambda b: b.net_paise, right_amount=lambda l: l.amount_paise,
        left_date=lambda b: b.settled_at.date(), right_date=lambda l: l.value_date,
        window=DATE_WINDOW_BANK, leg=Leg.BATCH_BANK, res=res,
        left_key=lambda b: b.settlement_id, right_key=lambda l: l.key,
        on_match=lambda b, l: (open_batches.pop(b.settlement_id, None),
                               open_lines.pop(l.key, None)),
        extra=lambda b, l: {"narration": l.narration})

    if adjudicator is not None and open_batches and open_lines:
        _adjudicate_bank(open_batches, open_lines, res, adjudicator)

    for batch in open_batches.values():
        res.findings.append(Finding(
            key=batch.settlement_id, source="settlement", code=ExceptionCode.MISSING_IN_BANK,
            disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=batch.net_paise,
            evidence={"utr": batch.utr, "settled_at": batch.settled_at.isoformat(),
                      "net": str(to_rupees(batch.net_paise)),
                      "members": len(batch.members),
                      "gross": str(to_rupees(batch.gross_paise)),
                      "fee_and_gst": str(to_rupees(batch.fee_paise + batch.tax_paise))},
            proposed_action=f"Trace UTR {batch.utr} with the bank. Until it lands, "
                            f"{to_rupees(batch.net_paise)} sits in gateway receivable, "
                            "not in cash."))
    for line in open_lines.values():
        res.findings.append(Finding(
            key=line.key, source="bank", code=ExceptionCode.UNEXPECTED_BANK_CREDIT,
            disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=line.amount_paise,
            evidence={"value_date": line.value_date.isoformat(),
                      "narration": line.narration, "ref_no": line.ref_no,
                      "amount": str(to_rupees(line.amount_paise)),
                      "claims_settlement": looks_like_settlement(line.narration)},
            proposed_action="Identify the payer before this is swept into the settlement "
                            "control account; it is not gateway money on the evidence here."))


def _classify_settlement_delta(batch: SettlementBatch, lines: list[BankLine],
                               total: int, delta: int) -> Finding:
    """Name the gap only when its size is consistent with a known cause.

    Every delta used to become SETTLEMENT_AMOUNT_MISMATCH with a confident note
    about a correspondent charge. Fed a foreign-currency settlement — a real
    case the taxonomy has no code for — it reported a bank charge of ₹4,200,
    which is a plausible sentence and the wrong answer. `UNCLASSIFIED` existed
    to prevent exactly that and had never once fired, because nothing could
    reach it.

    A charge or recovery is small relative to the batch. Beyond that the engine
    does not know what happened, and the honest output says so rather than
    picking the nearest label.
    """
    ceiling = max(CHARGE_TOL_PAISE, round(abs(batch.net_paise) * CHARGE_TOL_FRAC))
    evidence = {"utr": batch.utr, "batch_net": str(to_rupees(batch.net_paise)),
                "bank_credited": str(to_rupees(total)),
                "delta": str(to_rupees(delta)),
                "plausible_charge_up_to": str(to_rupees(ceiling)),
                "narration": lines[0].narration}
    if abs(delta) <= ceiling:
        return Finding(
            key=batch.settlement_id, source="settlement",
            code=ExceptionCode.SETTLEMENT_AMOUNT_MISMATCH,
            disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=delta,
            evidence=evidence,
            proposed_action=("Bank credited less than the batch netted to, by an amount "
                             "consistent with a correspondent charge; confirm with the bank."
                             if delta < 0 else
                             "Bank credited more than the batch netted to; check for a "
                             "prior shortfall being made good."))
    return Finding(
        key=batch.settlement_id, source="settlement", code=ExceptionCode.UNCLASSIFIED,
        disposition=Disposition.NEEDS_REVIEW, value_at_risk_paise=delta,
        evidence={**evidence, "why_unclassified":
                  "gap is too large to be a bank charge and matches no other known cause"},
        proposed_action=f"The bank credit differs from the batch by "
                        f"{to_rupees(abs(delta))}, which is more than a charge would "
                        "explain. Likely a currency conversion, a partial recall or a "
                        "netting the report does not show — needs the bank advice.")


def _match_merged_payouts(open_batches: dict, open_lines: dict, res: ReconResult) -> None:
    """Find a small set of batches that sums exactly to one bank credit.

    Bounded on purpose. Subset-sum over an unbounded set will always find *a*
    combination given enough rows, and a coincidental sum posted as a match is
    worse than an unmatched line. So: only credits whose narration claims to be
    gateway money, only batches settled within the date window, at most three
    batches, and an exact sum. If two subsets tie, the one spanning the fewest
    days wins, and if that still ties we refuse rather than guess.
    """
    for line in sorted(open_lines.values(), key=lambda l: l.line_no):
        if not looks_like_settlement(line.narration):
            continue
        pool = [b for b in open_batches.values()
                if _days(b.settled_at.date(), line.value_date) <= DATE_WINDOW_BANK]
        if len(pool) < 2:
            continue
        found: list[tuple[int, tuple]] = []
        for size in range(2, MAX_MERGE_SIZE + 1):
            for combo in combinations(pool, size):
                if abs(sum(b.net_paise for b in combo) - line.amount_paise) <= AMOUNT_TOL:
                    span = (max(b.settled_at.date() for b in combo)
                            - min(b.settled_at.date() for b in combo)).days
                    found.append((span, combo))
            if found:
                break
        if not found:
            continue
        found.sort(key=lambda sc: (sc[0], tuple(b.settlement_id for b in sc[1])))
        if len(found) > 1 and found[0][0] == found[1][0]:
            continue                       # ambiguous; leave it for a human
        combo = found[0][1]
        total = sum(b.net_paise for b in combo)
        res.matches.append(Match(
            Leg.BATCH_BANK, line.key, tuple(b.settlement_id for b in combo),
            Tier.AGGREGATE, 0.90, line.amount_paise - total,
            {"on": "subset_sum", "batches": [b.settlement_id for b in combo],
             "batch_nets": [str(to_rupees(b.net_paise)) for b in combo],
             "credit": str(to_rupees(line.amount_paise)), "narration": line.narration}))
        for b in combo:
            res.findings.append(Finding(
                key=b.settlement_id, source="settlement", code=ExceptionCode.MERGED_PAYOUT,
                disposition=Disposition.AUTO_RESOLVED, value_at_risk_paise=0,
                evidence={"bank_line": line.key, "narration": line.narration,
                          "credit_total": str(to_rupees(line.amount_paise)),
                          "this_batch": str(to_rupees(b.net_paise)),
                          "paid_with": [x.settlement_id for x in combo if x is not b],
                          "utr_absent_from_narration": True},
                proposed_action="No action: the consolidated credit reconciles exactly. Ask "
                                "the gateway to quote per-batch UTRs to avoid the search."))
            open_batches.pop(b.settlement_id, None)
        open_lines.pop(line.key, None)


def _recover_unreadable_narrations(open_batches: dict, open_lines: dict,
                                   res: ReconResult, adjudicator) -> None:
    """Ask the model to read a statement format nobody has written a pattern for.

    An earlier ablation found model adjudication on this leg worthless, and it
    was — on a corpus whose narrations all came from six templates the extractor
    already knew. Every residual there genuinely had no counterparty, so every
    answer was a false positive. Against statement formats the extractor cannot
    parse the residual *does* have a counterparty, and the same call earns its
    place. The rule was never about the leg; it was about whether anything was
    there to find.

    So it runs only where that is plausibly true: the line reads as gateway
    money, no reference could be extracted from it, and the amount is checked
    against the chosen batch before anything is posted.
    """
    for line in sorted(open_lines.values(), key=lambda l: l.line_no):
        if not looks_like_settlement(line.narration) or extract_utrs(line.narration):
            continue                       # a pattern already read it, or it is not ours
        cands = sorted(open_batches.values(),
                       key=lambda b: (abs(b.net_paise - line.amount_paise),
                                      _days(b.settled_at.date(), line.value_date)))[:3]
        if not cands:
            continue
        choice = adjudicator.read_narration(
            {"amount": str(to_rupees(line.amount_paise)),
             "value_date": line.value_date.isoformat(), "narration": line.narration},
            [{"key": b.settlement_id, "net": str(to_rupees(b.net_paise)),
              "settled_on": b.settled_at.date().isoformat(),
              "utr": b.utr} for b in cands])
        picked = next((b for b in cands if b.settlement_id == choice.get("choice")), None)
        verdict = "declined" if picked is None else "accepted"
        if picked is not None and (
                abs(picked.net_paise - line.amount_paise) > ADJUDICATION_TOL
                or _days(picked.settled_at.date(), line.value_date) > DATE_WINDOW_BANK):
            verdict = "rejected_by_arithmetic"
        res.adjudications.append({
            "leg": "unreadable_narration", "bank_line": line.key,
            "narration": line.narration,
            "candidates": [b.settlement_id for b in cands],
            "chose": choice.get("choice"), "reason": choice.get("reason", ""),
            "verdict": verdict})
        if verdict != "accepted":
            continue
        res.matches.append(Match(
            Leg.BATCH_BANK, picked.settlement_id, (line.key,), Tier.ADJUDICATED,
            float(choice.get("confidence", 0.6)), line.amount_paise - picked.net_paise,
            {"on": "model_read_the_narration", "narration": line.narration,
             "utr_not_extractable": True, "reason": choice.get("reason", ""),
             "candidates": [b.settlement_id for b in cands]}))
        open_batches.pop(picked.settlement_id, None)
        open_lines.pop(line.key, None)


def _adjudicate_bank(open_batches: dict, open_lines: dict, res: ReconResult, adjudicator) -> None:
    for batch in list(open_batches.values()):
        cands = sorted(open_lines.values(),
                       key=lambda l: (abs(l.amount_paise - batch.net_paise),
                                      _days(batch.settled_at.date(), l.value_date)))[:3]
        if not cands:
            break
        choice = adjudicator.choose_bank_line(
            {"settlement_id": batch.settlement_id, "net": str(to_rupees(batch.net_paise)),
             "settled_at": batch.settled_at.date().isoformat()},
            [{"key": c.key, "amount": str(to_rupees(c.amount_paise)),
              "value_date": c.value_date.isoformat(), "narration": c.narration}
             for c in cands])
        res.adjudications.append({"batch": batch.settlement_id,
                                  "candidates": [c.key for c in cands],
                                  "chose": choice.get("choice"),
                                  "reason": choice.get("reason", "")})
        picked = next((c for c in cands if c.key == choice.get("choice")), None)
        if picked is None or picked.key not in open_lines:
            continue
        if (abs(picked.amount_paise - batch.net_paise) > ADJUDICATION_TOL
                or not looks_like_settlement(picked.narration)):
            res.adjudications[-1]["verdict"] = "rejected_by_arithmetic"
            continue
        res.adjudications[-1]["verdict"] = "accepted"
        res.matches.append(Match(
            Leg.BATCH_BANK, batch.settlement_id, (picked.key,), Tier.ADJUDICATED,
            float(choice.get("confidence", 0.55)), picked.amount_paise - batch.net_paise,
            {"on": "model_adjudication", "reason": choice.get("reason", ""),
             "candidates": [c.key for c in cands]}))
        open_batches.pop(batch.settlement_id); open_lines.pop(picked.key)


# --------------------------------------------------------------------------- #
#  Leg D — unpaid invoice ↔ unexplained bank credit
# --------------------------------------------------------------------------- #

def _tds_relation(gross: int, credit: int) -> str | None:
    """Is this credit the invoice paid in full, or short by a statutory TDS rate?"""
    if abs(credit - gross) <= AMOUNT_TOL:
        return "gross"
    for bps in TDS_BPS:
        if abs(credit - (gross - round(gross * bps / 10_000))) <= AMOUNT_TOL:
            return f"net_of_tds_{bps / 100:g}pct"
    return None


def reconcile_direct_receipts(ds: Dataset, res: ReconResult, adjudicator=None) -> None:
    """Some customers pay the bank directly and never touch the gateway.

    The invoice then looks unpaid and the credit looks unexplained — two
    exceptions that are really one settled transaction. Nothing links them but
    the amount and a counterparty name inside free text, and Indian remitters
    routinely withhold TDS, so the amount does not have to agree either.

    Deterministic first: an exact amount relationship *and* a strong name
    overlap. Only where the name has been mangled past a token threshold is the
    model asked, and its answer is then re-checked against the same arithmetic —
    a candidate whose amount relationship does not hold is discarded however
    confident the model was.
    """
    inv_by_no = {i.invoice_no: i for i in ds.invoices}
    line_by_key = {b.key: b for b in ds.bank}
    unpaid = {f.key: f for f in res.findings if f.code is ExceptionCode.UNPAID_INVOICE}
    credits = {f.key: f for f in res.findings
               if f.code is ExceptionCode.UNEXPECTED_BANK_CREDIT}
    if not unpaid or not credits:
        return

    def feasible(inv: Invoice, line: BankLine) -> str | None:
        if not (0 <= (line.value_date - inv.invoice_date).days <= DIRECT_WINDOW):
            return None
        return _tds_relation(inv.gross_paise, line.amount_paise)

    resolved: set[str] = set()

    def settle(inv: Invoice, line: BankLine, rel: str, tier: Tier,
               confidence: float, overlap: float, note: str) -> None:
        res.matches.append(Match(
            Leg.INVOICE_BANK, inv.invoice_no, (line.key,), tier, confidence,
            line.amount_paise - inv.gross_paise,
            {"on": "narration+amount", "relation": rel, "customer": inv.customer,
             "narration": line.narration, "name_overlap": round(overlap, 3),
             "invoice_gross": str(to_rupees(inv.gross_paise)),
             "bank_credit": str(to_rupees(line.amount_paise)), "note": note}))
        resolved.update((inv.invoice_no, line.key))
        if rel != "gross":
            withheld = inv.gross_paise - line.amount_paise
            res.findings.append(Finding(
                key=inv.invoice_no, source="erp", code=ExceptionCode.TDS_WITHHELD,
                disposition=Disposition.AUTO_RESOLVED, value_at_risk_paise=withheld,
                evidence={"relation": rel, "bank_line": line.key,
                          "invoice_gross": str(to_rupees(inv.gross_paise)),
                          "received": str(to_rupees(line.amount_paise)),
                          "withheld": str(to_rupees(withheld)),
                          "customer": inv.customer, "matched_by": tier.value},
                proposed_action=f"Book {to_rupees(withheld)} as TDS receivable against "
                                f"{inv.customer} and collect Form 16A for the quarter."))

    # T2 — amount relationship plus a name the narration actually carries.
    for ikey in list(unpaid):
        if ikey in resolved:
            continue
        inv = inv_by_no[ikey]
        best = None
        for bkey in credits:
            if bkey in resolved:
                continue
            line = line_by_key[bkey]
            rel = feasible(inv, line)
            if not rel:
                continue
            ov = token_overlap(inv.customer, line.narration)
            if ov >= NAME_TOL and (best is None or ov > best[0]):
                best = (ov, line, rel)
        if best:
            ov, line, rel = best
            settle(inv, line, rel, Tier.AMOUNT_DATE, round(min(0.96, 0.75 + ov), 3), ov,
                   "counterparty name present in the narration")

    # T4 — the arithmetic works but the name is mangled. Ask the model, then verify.
    if adjudicator is not None:
        for bkey in list(credits):
            if bkey in resolved:
                continue
            line = line_by_key[bkey]
            # The model may only choose among candidates that carry at least one
            # literal word of the customer's name in the narration. Without this
            # gate a bank interest credit ("INT.PD:12345678:01-08-2026 TO
            # 31-08-2026") that happens to land within 2% of an open invoice
            # passes the TDS arithmetic by coincidence, and the model — which
            # correctly identified it as interest in its own reason — linked it
            # to a customer anyway. Seed 13 of the benchmark, before this line.
            cands = [inv_by_no[k] for k in unpaid
                     if k not in resolved and feasible(inv_by_no[k], line)
                     and token_overlap(inv_by_no[k].customer, line.narration) > 0]
            if not cands:
                continue
            cands = sorted(cands, key=lambda i: abs(i.gross_paise - line.amount_paise))[:3]
            choice = adjudicator.choose_invoice(
                {"amount": str(to_rupees(line.amount_paise)),
                 "value_date": line.value_date.isoformat(), "narration": line.narration},
                [{"key": i.invoice_no, "customer": i.customer,
                  "gross": str(to_rupees(i.gross_paise)),
                  "invoice_date": i.invoice_date.isoformat()} for i in cands])
            picked = next((i for i in cands if i.invoice_no == choice.get("choice")), None)
            verdict = "accepted"
            rel = feasible(picked, line) if picked else None
            if picked is None:
                verdict = "declined"
            elif rel is None:                      # the model chose something that does not add up
                verdict = "rejected_by_arithmetic"
            res.adjudications.append({
                "leg": "invoice_to_bank", "bank_line": line.key,
                "narration": line.narration,
                "candidates": [i.invoice_no for i in cands],
                "chose": choice.get("choice"), "reason": choice.get("reason", ""),
                "verdict": verdict})
            if verdict == "accepted":
                settle(picked, line, rel, Tier.ADJUDICATED,
                       float(choice.get("confidence", 0.6)),
                       token_overlap(picked.customer, line.narration),
                       "model read the counterparty name; amount relationship verified")

    res.findings = [f for f in res.findings
                    if not (f.key in resolved
                            and f.code in (ExceptionCode.UNPAID_INVOICE,
                                           ExceptionCode.UNEXPECTED_BANK_CREDIT))]


# --------------------------------------------------------------------------- #

def _suggest_causes(res: ReconResult, adjudicator) -> None:
    """Attach a hypothesis to breaks the taxonomy has no code for.

    It is written into `hypothesis`, not into `code`. A guess promoted to a
    category is precisely what UNCLASSIFIED exists to prevent, so the finding
    stays unclassified and the sentence sits beside it, marked as a maybe.
    """
    for f in res.findings:
        if f.code is not ExceptionCode.UNCLASSIFIED:
            continue
        guess = adjudicator.propose_cause({**f.evidence, "gap": str(to_rupees(
            f.value_at_risk_paise))})
        if guess:
            f.evidence["hypothesis"] = guess


def reconcile(ds: Dataset, batches: list[SettlementBatch], adjudicator=None,
              adjudicate_legs: frozenset | None = None,
              confirmed: "set[tuple[str, str, str]] | None" = None) -> ReconResult:
    """`adjudicate_legs` selects which legs may consult the model. Defaults to
    the one leg where a model answer can be verified against arithmetic; see
    docs/architecture.md for the measurement behind that default."""
    legs = DEFAULT_ADJUDICATED_LEGS if adjudicate_legs is None else adjudicate_legs
    # Links a controller has already confirmed by hand. They are passed in
    # rather than read from anywhere, so the engine stays free of hidden state
    # and the confirmations are visible in the call that used them.
    confirmed = confirmed or set()
    pick = lambda leg: adjudicator if (adjudicator is not None and leg in legs) else None  # noqa: E731
    res = ReconResult()
    t0 = time.perf_counter()
    _apply_confirmed(ds, batches, confirmed, res)
    reconcile_erp_to_gateway(ds, res, pick(Leg.ERP_PG), res.confirmed_keys)
    t1 = time.perf_counter()
    check_integrity(ds, res)
    t2 = time.perf_counter()
    reconcile_settlement_to_bank(batches, ds, res, pick(Leg.BATCH_BANK))
    t3 = time.perf_counter()
    # Runs last: it consumes the exceptions the first three legs produced.
    reconcile_direct_receipts(ds, res, pick(Leg.INVOICE_BANK))
    if adjudicator is not None:
        _suggest_causes(res, adjudicator)
    t4 = time.perf_counter()

    # A record can reach the same conclusion by two routes — a re-sent export
    # row, or a leg that overlaps another. The controller should see it once.
    unique, seen_findings = [], set()
    for f in res.findings:
        if (f.key, f.code) in seen_findings:
            continue
        seen_findings.add((f.key, f.code))
        unique.append(f)
    res.findings = unique

    res.timings = {"erp_to_gateway_s": round(t1 - t0, 4),
                   "integrity_s": round(t2 - t1, 4),
                   "settlement_to_bank_s": round(t3 - t2, 4),
                   "direct_receipts_s": round(t4 - t3, 4),
                   "total_s": round(t4 - t0, 4)}
    res.counts = {**ds.counts(), "settlement_batches": len(batches),
                  "matches": len(res.matches), "findings": len(res.findings),
                  "unresolved": len(res.unresolved())}
    return res
