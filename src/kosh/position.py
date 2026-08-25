"""The settlement control account, rolled forward.

Reconciling records is only half of a controller's question. The other half is
*where is the money*, and the answer is rarely 'the bank balance'. Between a
customer's card being charged and cash being spendable there is a gateway
holding fees, a batch in flight, a dispute reserve and, if the reconciliation
found exceptions, some amount that nobody can currently account for.

This builds that bridge as an explicit roll-forward, so every rupee between
'captured' and 'landed' is on a named line. The identity that has to hold:

    gross entering settlement − refunds − fees − GST − disputes = Σ batch nets

If it does not hold, `residual` is non-zero and the report says so rather than
plugging the difference. It caught a real bug in this very function; see
`build_position`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .match import Disposition, Leg, ReconResult
from .money import to_rupees
from .schema import Dataset, ExceptionCode, SettlementBatch


@dataclass
class Position:
    captured: int = 0                # gross of every capture in the period
    on_hold_gross: int = 0           # captured but held by the gateway
    unbatched_gross: int = 0         # captured, not held, not yet in a batch
    gross_entering_settlement: int = 0
    refunds_settled: int = 0
    gateway_fees: int = 0            # on settled captures only
    fee_gst: int = 0
    dispute_adjustments: int = 0
    settled_net: int = 0             # derived from the rows
    batch_net: int = 0               # what the batches themselves sum to
    residual: int = 0                # settled_net − batch_net; must be zero
    landed_in_bank: int = 0
    in_transit: int = 0
    unexplained_credits: int = 0
    open_receivables: int = 0
    unbilled_revenue: int = 0
    tds_receivable: int = 0

    def to_json(self) -> dict:
        return {k: str(to_rupees(v)) for k, v in self.__dict__.items()}


def build_position(ds: Dataset, batches: list[SettlementBatch],
                   res: ReconResult) -> Position:
    """Classify every gateway row by where its money actually is.

    A capture is in exactly one of four states — held, awaiting batching,
    batched, or landed — and fees are only charged on the ones that batched.
    An earlier version of this netted fees against held captures too, which
    produced a residual of a few thousand rupees that looked like a real
    reconciliation break. It was not; it was this function double-counting.
    Keeping `residual` on the face of the report is what surfaced it.
    """
    p = Position()

    for t in ds.pg:
        if t.type == "payment":
            p.captured += t.amount_paise
            if t.on_hold:
                p.on_hold_gross += t.amount_paise
            elif not t.settlement_id:
                p.unbatched_gross += t.amount_paise
            else:
                p.gross_entering_settlement += t.amount_paise
                p.gateway_fees += t.fee_paise
                p.fee_gst += t.tax_paise
        elif t.type == "refund" and t.settlement_id:
            p.refunds_settled += abs(t.amount_paise)
        elif t.type == "adjustment" and t.settlement_id:
            p.dispute_adjustments += abs(t.amount_paise)

    p.settled_net = (p.gross_entering_settlement - p.refunds_settled
                     - p.gateway_fees - p.fee_gst - p.dispute_adjustments)
    p.batch_net = sum(b.net_paise for b in batches)
    p.residual = p.settled_net - p.batch_net

    landed_batches = set()
    for m in res.matches:
        if m.leg is not Leg.BATCH_BANK:
            continue
        landed_batches.update(m.right if m.left.startswith("bank:") else [m.left])
    by_id = {b.settlement_id: b for b in batches}
    p.landed_in_bank = sum(by_id[s].net_paise for s in landed_batches if s in by_id)
    p.in_transit = sum(b.net_paise for b in batches
                       if b.settlement_id not in landed_batches)

    for f in res.findings:
        if f.code is ExceptionCode.UNEXPECTED_BANK_CREDIT:
            p.unexplained_credits += f.value_at_risk_paise
        elif f.code is ExceptionCode.UNPAID_INVOICE:
            p.open_receivables += f.value_at_risk_paise
        elif f.code is ExceptionCode.UNBILLED_PAYMENT:
            p.unbilled_revenue += f.value_at_risk_paise
        elif f.code is ExceptionCode.TDS_WITHHELD:
            p.tds_receivable += f.value_at_risk_paise

    return p


def bridge_rows(p: Position) -> list[tuple[str, int, str]]:
    """The roll-forward as ordered display rows: (label, paise, kind)."""
    return [
        ("Captured at the gateway", p.captured, "in"),
        ("Less funds on hold", -p.on_hold_gross, "out"),
        ("Less captures not yet batched", -p.unbatched_gross, "out"),
        ("= Gross entering settlement", p.gross_entering_settlement, "subtotal"),
        ("Less refunds settled", -p.refunds_settled, "out"),
        ("Less gateway fees", -p.gateway_fees, "out"),
        ("Less GST on fees", -p.fee_gst, "out"),
        ("Less dispute adjustments", -p.dispute_adjustments, "out"),
        ("= Net settled into batches", p.settled_net, "subtotal"),
        ("Batches say", p.batch_net, "check"),
        ("Residual (must be zero)", p.residual, "warn" if p.residual else "ok"),
        ("Landed in the bank", p.landed_in_bank, "in"),
        ("Still in transit", p.in_transit, "warn"),
    ]
