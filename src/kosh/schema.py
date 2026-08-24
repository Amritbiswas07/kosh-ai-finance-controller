"""The three sources Kosh reconciles, and the exception taxonomy.

Column names on `PGTxn` deliberately mirror Razorpay's real settlement
recon report (entity_id / type / debit / credit / fee / tax / on_hold /
settled / settlement_id / settlement_utr / order_receipt / method ...), so the
parser that reads synthetic data is the same parser that would read an exported
one. `BankLine` mirrors a plain Indian current-account statement export, which
is where most of the mess lives: the UTR is buried in a free-text narration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Source(str, Enum):
    ERP = "erp"
    PG = "pg"
    BANK = "bank"


class ExceptionCode(str, Enum):
    """Every unresolved item lands in exactly one of these.

    The taxonomy is closed on purpose: an open-ended 'other' bucket lets an
    engine hide its failures in prose. If a residual does not fit a code, that
    is a gap in the taxonomy and should be visible as UNCLASSIFIED.
    """

    MISSING_IN_BANK = "MISSING_IN_BANK"
    UNEXPECTED_BANK_CREDIT = "UNEXPECTED_BANK_CREDIT"
    SETTLEMENT_AMOUNT_MISMATCH = "SETTLEMENT_AMOUNT_MISMATCH"
    FEE_VARIANCE = "FEE_VARIANCE"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    UNBILLED_PAYMENT = "UNBILLED_PAYMENT"
    UNPAID_INVOICE = "UNPAID_INVOICE"
    ORPHAN_REFUND = "ORPHAN_REFUND"
    FUNDS_ON_HOLD = "FUNDS_ON_HOLD"
    TAX_LINE_MISMATCH = "TAX_LINE_MISMATCH"
    SPLIT_SETTLEMENT = "SPLIT_SETTLEMENT"
    CHARGEBACK_ADJUSTMENT = "CHARGEBACK_ADJUSTMENT"
    MERGED_PAYOUT = "MERGED_PAYOUT"
    TDS_WITHHELD = "TDS_WITHHELD"
    UNCLASSIFIED = "UNCLASSIFIED"


#: Human-facing one-liners. Used by the report and as LLM grounding.
EXCEPTION_MEANING: dict[ExceptionCode, str] = {
    ExceptionCode.MISSING_IN_BANK: "A settlement batch left the gateway but no matching bank credit has landed.",
    ExceptionCode.UNEXPECTED_BANK_CREDIT: "Money arrived in the bank that no settlement batch accounts for.",
    ExceptionCode.SETTLEMENT_AMOUNT_MISMATCH: "The bank credited a different amount than the settlement batch netted to.",
    ExceptionCode.FEE_VARIANCE: "The gateway fee charged differs from the contracted MDR for that method.",
    ExceptionCode.DUPLICATE_PAYMENT: "One order was paid for more than once.",
    ExceptionCode.UNBILLED_PAYMENT: "A payment was captured with no invoice behind it — revenue recognised nowhere.",
    ExceptionCode.UNPAID_INVOICE: "An invoice was raised but no payment was ever captured against it.",
    ExceptionCode.ORPHAN_REFUND: "A refund exists whose original payment is not in the data.",
    ExceptionCode.FUNDS_ON_HOLD: "A captured payment is held by the gateway and will not settle yet.",
    ExceptionCode.TAX_LINE_MISMATCH: "Invoice GST does not equal the statutory rate applied to the taxable value.",
    ExceptionCode.SPLIT_SETTLEMENT: "One settlement batch arrived as more than one bank credit.",
    ExceptionCode.CHARGEBACK_ADJUSTMENT: "The gateway debited a dispute or reserve adjustment the ERP does not carry.",
    ExceptionCode.MERGED_PAYOUT: "Several settlement batches arrived as a single consolidated bank credit.",
    ExceptionCode.TDS_WITHHELD: "A customer paid the invoice net of tax deducted at source.",
    ExceptionCode.UNCLASSIFIED: "The engine could not place this residual in any known category.",
}

#: Contracted MDR in basis points by method, plus GST on the fee.
MDR_BPS: dict[str, int] = {
    "upi": 0,
    "netbanking": 150,
    "card": 200,
    "amex": 350,
    "wallet": 180,
    "emi": 300,
}
FEE_GST_BPS = 1800  # 18% GST charged on the gateway fee itself


@dataclass(frozen=True)
class Invoice:
    """A line from the merchant's ERP / accounting system."""

    invoice_no: str
    order_id: str
    customer: str
    invoice_date: date
    taxable_paise: int
    tax_paise: int
    gross_paise: int
    currency: str = "INR"

    @property
    def key(self) -> str:
        return self.invoice_no


@dataclass(frozen=True)
class PGTxn:
    """A row of the gateway settlement recon report."""

    entity_id: str            # pay_… / rfnd_… / adjs_…
    type: str                 # payment | refund | adjustment
    amount_paise: int         # signed: credit positive, debit negative
    fee_paise: int
    tax_paise: int            # GST on the fee
    created_at: datetime
    method: str
    order_id: str | None = None
    order_receipt: str | None = None
    settlement_id: str | None = None
    settlement_utr: str | None = None
    settled_at: datetime | None = None
    on_hold: bool = False
    dispute_id: str | None = None
    parent_payment_id: str | None = None

    @property
    def key(self) -> str:
        return self.entity_id

    @property
    def settled(self) -> bool:
        return self.settlement_id is not None

    @property
    def net_paise(self) -> int:
        """What this row contributes to the settlement batch total."""
        return self.amount_paise - self.fee_paise - self.tax_paise


@dataclass(frozen=True)
class BankLine:
    """A line from the bank statement export."""

    line_no: int
    value_date: date
    narration: str
    ref_no: str
    amount_paise: int         # signed: credit positive, debit negative
    balance_paise: int

    @property
    def key(self) -> str:
        return f"bank:{self.line_no:04d}"

    @property
    def is_credit(self) -> bool:
        return self.amount_paise > 0


@dataclass(frozen=True)
class SettlementBatch:
    """Derived, not a source: the gateway's rows grouped by settlement_id."""

    settlement_id: str
    utr: str
    settled_at: datetime
    members: tuple[str, ...]          # entity_ids
    gross_paise: int
    fee_paise: int
    tax_paise: int
    net_paise: int

    @property
    def key(self) -> str:
        return self.settlement_id


@dataclass
class Dataset:
    invoices: list[Invoice] = field(default_factory=list)
    pg: list[PGTxn] = field(default_factory=list)
    bank: list[BankLine] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.invoices) + len(self.pg) + len(self.bank)

    def counts(self) -> dict[str, int]:
        return {
            "erp_invoices": len(self.invoices),
            "pg_transactions": len(self.pg),
            "bank_lines": len(self.bank),
            "total_records": len(self),
        }
