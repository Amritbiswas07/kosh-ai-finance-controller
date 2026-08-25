"""Synthetic three-source finance data, with ground truth.

The point of generating rather than hand-writing the corpus is that every link
and every exception is *known* before the engine runs. `generate` writes four
files: three that look like real exports (ERP invoice register, gateway
settlement recon report, bank statement) and one — `ground_truth.json` — that
the reconciliation engine never opens. Only `kosh evaluate` opens it.

Exceptions are injected on purpose, in stated counts, so a claimed match rate
can be checked against a denominator that was fixed in advance. Nothing here
is tuned to make the engine look good: the messy narration formats, the split
settlements and the fee drift were all written before the matcher existed.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .currency import BASE, Money, Rate, RateTable, write_rates
from .money import to_paise
from .schema import FEE_GST_BPS, MDR_BPS, BankLine, Dataset, ExceptionCode, Invoice, PGTxn

CUSTOMERS = [
    "Anand Traders", "Bharat Textiles", "Chetna Organics", "Deccan Foods",
    "Everest Logistics", "Frontier Optics", "Gokul Dairy", "Himalaya Wellness",
    "Indus Motors", "Jyoti Electricals", "Kaveri Seeds", "Lotus Interiors",
    "Meridian Labs", "Nilgiri Tea Co", "Orion Sportswear", "Prabhat Printers",
    "Quantum Fasteners", "Rashmi Jewellers", "Sagar Marine", "Trident Cables",
    "Udupi Kitchens", "Vindhya Ceramics", "Windward Sails", "Yamuna Paper",
]
METHODS = ["upi", "upi", "upi", "card", "card", "netbanking", "wallet", "emi", "amex"]
BANK_CODES = ["HDFCN", "ICICN", "SBIN0", "AXISN", "KKBKN"]

NARRATION_TEMPLATES = [
    "NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD-{ifsc}",
    "IMPS/{utr}/RAZORPAYSOFT/SETTLEMENT",
    "MB:NEFT CR-{utr}-RAZORPAY-SETTLEMENT",
    "RTGS {utr} RAZORPAY SOFTWARE PRIVATE LIMITED",
    "NEFT CR {utr} RAZORPAY SOFTWARE  PVT  LTD",
    "ACH/C/{utr}/RAZORPAY SOFTWARE",
]
NOISE_NARRATIONS = [
    ("SALARY PAYOUT AUG BATCH 1", -1),
    ("RENT-UNIT 402 SKYVIEW-MAR", -1),
    ("GST PMT-06 CHALLAN 24081", -1),
    ("VENDOR PMT MEHTA STEEL LLP", -1),
    ("INT.PD:12345678:01-08-2026 TO 31-08-2026", 1),
    ("NEFT-CITIN25081200099-ANAND TRADERS-DIRECT", 1),
    ("TERM LOAN DISBURSAL TL-99321", 1),
    ("CHQ PAID-004512", -1),
    ("UPI/DR/558712330091/SWIGGY/OFFICE MEALS", -1),
    ("NACH DR ICICI PRU LIFE PREMIUM", -1),
]


@dataclass
class Injections:
    """How many of each defect to plant. Sums into the ground-truth denominator."""

    unpaid_invoice: int = 6
    unbilled_payment: int = 5
    duplicate_payment: int = 4
    orphan_refund: int = 3
    funds_on_hold: int = 4
    fee_variance: int = 5
    tax_line_mismatch: int = 4
    chargeback_adjustment: int = 3
    missing_in_bank: int = 3
    settlement_amount_mismatch: int = 3
    split_settlement: int = 2
    unexpected_bank_credit: int = 4
    merged_payout: int = 2   # pairs of batches paid as one credit
    missing_order_id: int = 5  # payment exported with no order reference at all
    direct_bank_payment: int = 3  # customer paid the bank directly, bypassing the gateway
    tds_deduction: int = 3        # direct payment net of TDS, counterparty name mangled
    short_payment: int = 3        # customer underpaid the invoice
    overpayment: int = 2          # customer overpaid it
    part_payment: int = 3         # invoice settled by instalments that add up
    foreign_invoice: int = 6      # invoiced abroad, settled after conversion

    def total(self) -> int:
        return sum(getattr(self, f) for f in self.__dataclass_fields__)

    @classmethod
    def jittered(cls, rng: random.Random, spread: float = 0.6,
                 scale: float = 1.0) -> "Injections":
        """Vary every defect rate for this seed.

        Without this the corpus has the same number of each defect every time,
        so a benchmark over many seeds measures the same difficulty repeatedly.
        Scaling each count by a random factor makes some months messy and some
        nearly clean, which is what a real close looks like.
        """
        base = cls()
        return cls(**{f: max(0, round(getattr(base, f) * scale
                                      * rng.uniform(1 - spread, 1 + spread)))
                      for f in base.__dataclass_fields__})


@dataclass
class GroundTruth:
    """What is actually true. The engine must never read this."""

    invoice_to_payment: dict[str, list[str]] = field(default_factory=dict)
    payment_to_batch: dict[str, str] = field(default_factory=dict)
    invoice_to_bank: dict[str, str] = field(default_factory=dict)
    batch_to_bank: dict[str, list[str]] = field(default_factory=dict)
    exceptions: dict[str, str] = field(default_factory=dict)   # record key -> code
    rates: object = None                    # the FX table this world used
    notes: dict[str, str] = field(default_factory=dict)        # record key -> why

    def flag(self, key: str, code: ExceptionCode, why: str) -> None:
        self.exceptions[key] = code.value
        self.notes[key] = why

    def to_json(self) -> dict:
        return {
            "invoice_to_payment": self.invoice_to_payment,
            "payment_to_batch": self.payment_to_batch,
            "invoice_to_bank": self.invoice_to_bank,
            "batch_to_bank": self.batch_to_bank,
            "exceptions": self.exceptions,
            "notes": self.notes,
        }


def _expected_fee(amount_paise: int, method: str) -> tuple[int, int]:
    """Contracted fee and the GST charged on that fee, both in paise."""
    fee = round(amount_paise * MDR_BPS.get(method, 200) / 10_000)
    tax = round(fee * FEE_GST_BPS / 10_000)
    return fee, tax


def _repriced(p: PGTxn, amount: int) -> PGTxn:
    """Change a capture's amount and recharge the fee to match.

    The gateway's fee is a percentage of what it actually processed, so editing
    the amount without recomputing the fee left every altered row looking like
    an MDR variance — nine false positives, and the engine was right each time.
    """
    fee, tax = _expected_fee(amount, p.method)
    return PGTxn(**{**p.__dict__, "amount_paise": amount,
                    "fee_paise": fee, "tax_paise": tax})


def _abbreviate(name: str) -> str:
    """Squeeze a counterparty name the way a remitter's own bank does.

    'Bharat Textiles' becomes 'BHARAT TXTLS' — first word intact, later words
    stripped of vowels. Enough signal for a person to recognise, not enough for
    a token-overlap threshold to accept on its own.
    """
    words = name.upper().split()
    out = [words[0]]
    for w in words[1:]:
        squeezed = w[0] + "".join(c for c in w[1:] if c not in "AEIOU")
        out.append(squeezed[:6])
    return " ".join(out)


def _utr(rng: random.Random, when: date) -> str:
    return f"{rng.choice(BANK_CODES)}{when:%y%m%d}{rng.randrange(10**5, 10**6)}"


def _rate_table(rng: random.Random, start: date) -> RateTable:
    """A month of dated reference rates.

    The generator owns these for the same reason it owns everything else: if it
    knows the rate on both days, it knows what the revaluation must come to, and
    can put that in the answer key rather than trusting the engine's arithmetic
    to check itself.
    """
    table = RateTable()
    for cur, opening in (("USD", Decimal("83.10")), ("EUR", Decimal("90.20"))):
        rate = opening
        for n in range(0, 70, 3):
            drift = Decimal(rng.uniform(-0.012, 0.012)).quantize(Decimal("0.00001"))
            rate = (rate * (Decimal(1) + drift)).quantize(Decimal("0.0001"))
            table.add(Rate(cur, BASE, rate, start + timedelta(days=n),
                           "RBI reference"))
    return table


def build(seed: int = 20260824, n_orders: int = 140, inj: Injections | None = None
          ) -> tuple[Dataset, GroundTruth, Injections]:
    rng = random.Random(seed)
    inj = inj or Injections()
    gt = GroundTruth()
    ds = Dataset()

    start = date(2026, 7, 1)
    rates = _rate_table(random.Random(seed ^ 0x5F), start)
    orders: list[dict] = []
    for i in range(n_orders):
        day = start + timedelta(days=rng.randrange(0, 45))
        taxable = to_paise(f"{rng.randrange(45_000, 9_50_000) / 100:.2f}")
        tax = round(taxable * 1800 / 10_000)
        orders.append({
            "order_id": f"order_{seed % 1000:03d}{i:04d}",
            "receipt": f"INV-2627-{i + 1001}",
            "customer": rng.choice(CUSTOMERS),
            "date": day,
            "taxable": taxable,
            "tax": tax,
            "gross": taxable + tax,
            "method": rng.choice(METHODS),
            "currency": "INR",
        })

    # Pick disjoint order slices for each order-level defect.
    pool = list(range(n_orders))
    rng.shuffle(pool)
    # Clamped: at high defect density the request can exceed the orders that
    # exist. Silently popping an empty pool raised IndexError; silently
    # over-promising would be worse, so `inj` is rewritten below to record what
    # was actually placed rather than what was asked for.
    take = lambda n: [pool.pop() for _ in range(min(n, len(pool)))]   # noqa: E731
    s_unpaid = set(take(inj.unpaid_invoice))
    s_unbilled = set(take(inj.unbilled_payment))
    s_dupe = set(take(inj.duplicate_payment))
    s_hold = set(take(inj.funds_on_hold))
    s_feevar = set(take(inj.fee_variance))
    s_taxbad = set(take(inj.tax_line_mismatch))
    s_noid = set(take(inj.missing_order_id))
    s_direct = set(take(inj.direct_bank_payment))
    s_tds = set(take(inj.tds_deduction))
    s_short = set(take(inj.short_payment))
    s_over = set(take(inj.overpayment))
    s_part = set(take(inj.part_payment))
    s_fx = set(take(inj.foreign_invoice))
    s_refund = set(take(10))                                   # normal, matched refunds

    inj = replace(inj, unpaid_invoice=len(s_unpaid), unbilled_payment=len(s_unbilled),
                  duplicate_payment=len(s_dupe), funds_on_hold=len(s_hold),
                  fee_variance=len(s_feevar), tax_line_mismatch=len(s_taxbad),
                  missing_order_id=len(s_noid), direct_bank_payment=len(s_direct),
                  tds_deduction=len(s_tds), short_payment=len(s_short),
                  overpayment=len(s_over), part_payment=len(s_part),
                  foreign_invoice=len(s_fx))

    payments: list[PGTxn] = []
    direct_receipts: list[tuple[dict, bool]] = []
    pay_seq = 0

    def new_payment(o: dict, *, on_hold: bool, fee_drift: int = 0) -> PGTxn:
        nonlocal pay_seq
        pay_seq += 1
        fee, tax = _expected_fee(o["gross"], o["method"])
        fee += fee_drift
        if fee_drift:
            tax = round(fee * FEE_GST_BPS / 10_000)
        created = datetime.combine(o["date"], datetime.min.time()) + timedelta(
            hours=rng.randrange(6, 23), minutes=rng.randrange(0, 60))
        return PGTxn(
            entity_id=f"pay_{seed % 1000:03d}{pay_seq:05d}",
            type="payment", amount_paise=o["gross"], fee_paise=fee, tax_paise=tax,
            created_at=created, method=o["method"], order_id=o["order_id"],
            order_receipt=o["receipt"], on_hold=on_hold,
            currency=o.get("currency", "INR"),
        )

    for idx, o in enumerate(orders):
        # --- ERP side -------------------------------------------------------
        if idx in s_fx:
            # Re-denominate the whole order abroad. Minor units happen to match
            # for USD and EUR, so the figures stay the same size; what changes
            # is that they are no longer rupees, and the settlement rate will
            # differ from the invoice rate.
            o["currency"] = rng.choice(["USD", "EUR"])
            o["taxable"] = round(o["taxable"] / 80)
            o["tax"] = 0                     # exports are zero-rated for GST
            o["gross"] = o["taxable"]

        if idx not in s_unbilled:
            tax = o["tax"]
            if idx in s_taxbad:
                tax = round(o["taxable"] * rng.choice([500, 1200, 2800]) / 10_000)
            ds.invoices.append(Invoice(
                invoice_no=o["receipt"], order_id=o["order_id"], customer=o["customer"],
                invoice_date=o["date"], taxable_paise=o["taxable"], tax_paise=tax,
                gross_paise=o["taxable"] + tax,
                currency=o.get("currency", "INR"),
            ))
            o["gross"] = o["taxable"] + tax      # the customer pays what it says
            if idx in s_taxbad:
                gt.flag(o["receipt"], ExceptionCode.TAX_LINE_MISMATCH,
                        f"GST on the invoice is not 18% of taxable value {o['taxable']}p.")

        # --- gateway side ---------------------------------------------------
        if idx in s_direct or idx in s_tds:
            direct_receipts.append((o, idx in s_tds))
            continue
        if idx in s_unpaid:
            gt.flag(o["receipt"], ExceptionCode.UNPAID_INVOICE,
                    "Invoice raised; no payment was ever captured for this order.")
            continue

        drift = rng.choice([-1_50, 2_30, 4_10, -3_05, 7_50]) if idx in s_feevar else 0
        p = new_payment(o, on_hold=idx in s_hold, fee_drift=drift)

        # Deliberately imperfect payments. The gaps avoid 1%, 2% and 10% so they
        # cannot be mistaken for a statutory TDS deduction.
        if idx in s_short:
            gap = round(o["gross"] * rng.choice([500, 700, 1300]) / 10_000)
            p = _repriced(p, o["gross"] - gap)
            gt.flag(o["receipt"], ExceptionCode.SHORT_PAYMENT,
                    f"Customer paid {o['gross'] - gap}p against a {o['gross']}p invoice.")
        elif idx in s_over:
            extra = round(o["gross"] * rng.choice([400, 900]) / 10_000)
            p = _repriced(p, o["gross"] + extra)
            gt.flag(o["receipt"], ExceptionCode.OVERPAYMENT,
                    f"Customer paid {o['gross'] + extra}p against a {o['gross']}p invoice.")
        if idx in s_noid:
            # The gateway export carries no order reference — a real and common
            # case (payment links, invoices raised outside the order flow). The
            # invoice→payment link still exists and must be recovered from
            # amount and date alone, so this is not an exception: it is a test
            # of whether the matcher can work without an identifier.
            p = PGTxn(**{**p.__dict__, "order_id": None, "order_receipt": None})
        payments.append(p)
        if idx not in s_unbilled:
            gt.invoice_to_payment.setdefault(o["receipt"], []).append(p.entity_id)
        else:
            gt.flag(p.entity_id, ExceptionCode.UNBILLED_PAYMENT,
                    "Payment captured against an order with no invoice in the ERP.")
        if idx in s_feevar:
            gt.flag(p.entity_id, ExceptionCode.FEE_VARIANCE,
                    f"Fee charged deviates from the contracted {MDR_BPS[o['method']]}bps MDR.")
        if idx in s_hold:
            gt.flag(p.entity_id, ExceptionCode.FUNDS_ON_HOLD,
                    "Gateway is holding these funds; the batch will not carry them.")
        if idx in s_part:
            first = round(o["gross"] * rng.choice([0.4, 0.55, 0.65]))
            payments[-1] = _repriced(p, first)
            second = _repriced(p, o["gross"] - first)
            payments.append(PGTxn(**{
                **second.__dict__, "entity_id": p.entity_id + "b",
                "created_at": p.created_at + timedelta(days=rng.randrange(2, 9))}))
            gt.invoice_to_payment.setdefault(o["receipt"], []).append(p.entity_id + "b")
            gt.flag(o["receipt"], ExceptionCode.PART_PAYMENT,
                    f"Invoice settled by two captures adding to {o['gross']}p.")

        if idx in s_dupe:
            d = new_payment(o, on_hold=False)
            d = PGTxn(**{**d.__dict__,
                         "created_at": p.created_at + timedelta(
                             hours=rng.randrange(1, 30), minutes=rng.randrange(1, 59))})
            payments.append(d)
            gt.flag(d.entity_id, ExceptionCode.DUPLICATE_PAYMENT,
                    f"Second capture on order {o['order_id']}, already paid by {p.entity_id}.")

    # --- refunds ------------------------------------------------------------
    refunds: list[PGTxn] = []
    settleable = [p for p in payments if not p.on_hold]
    for n, idx in enumerate(sorted(s_refund)):
        parents = [p for p in settleable
                   if p.order_id == orders[idx]["order_id"] and idx not in s_noid]
        if not parents:
            continue
        parent = parents[0]
        amt = -(parent.amount_paise if n % 3 == 0 else round(parent.amount_paise / 2))
        refunds.append(PGTxn(
            entity_id=f"rfnd_{seed % 1000:03d}{n:05d}", type="refund", amount_paise=amt,
            fee_paise=0, tax_paise=0,
            created_at=parent.created_at + timedelta(days=rng.randrange(1, 6)),
            method=parent.method, order_id=parent.order_id,
            parent_payment_id=parent.entity_id,
        ))
    for n in range(inj.orphan_refund):
        ghost = f"pay_{seed % 1000:03d}9{n:04d}"
        rid = f"rfnd_{seed % 1000:03d}9{n:04d}"
        refunds.append(PGTxn(
            entity_id=rid, type="refund",
            amount_paise=-to_paise(f"{rng.randrange(20_000, 4_00_000) / 100:.2f}"),
            fee_paise=0, tax_paise=0,
            created_at=datetime(2026, 8, 1, 11, 0) + timedelta(days=n),
            method="card",
            order_id=None, parent_payment_id=ghost,
        ))
        gt.flag(rid, ExceptionCode.ORPHAN_REFUND,
                f"Refund references payment {ghost}, which is not present in the report.")

    # --- chargeback / reserve adjustments ------------------------------------
    adjustments: list[PGTxn] = []
    for n in range(inj.chargeback_adjustment):
        aid = f"adjs_{seed % 1000:03d}{n:05d}"
        adjustments.append(PGTxn(
            entity_id=aid, type="adjustment",
            amount_paise=-to_paise(f"{rng.randrange(50_000, 3_00_000) / 100:.2f}"),
            fee_paise=0, tax_paise=0,
            created_at=datetime(2026, 8, 3, 9, 30) + timedelta(days=n * 4),
            method="card",
            dispute_id=f"disp_{seed % 1000:03d}{n:04d}",
        ))
        gt.flag(aid, ExceptionCode.CHARGEBACK_ADJUSTMENT,
                "Dispute adjustment debited by the gateway; no matching ERP entry.")

    # --- settle everything that is settleable into daily batches -------------
    # A payout is per day *and per currency*: a gateway does not pay dollars and
    # rupees in one transfer, and a batch that summed both would have a net that
    # is not a quantity.
    to_settle = [t for t in payments + refunds + adjustments if not t.on_hold]
    by_day: dict[tuple[date, str], list[PGTxn]] = {}
    for t in to_settle:
        settle_day = (t.created_at + timedelta(days=2)).date()
        by_day.setdefault((settle_day, t.currency), []).append(t)

    settled_rows: list[PGTxn] = []
    batches: list[dict] = []
    for n, (day, currency) in enumerate(sorted(by_day)):
        members = by_day[(day, currency)]
        sid = f"setl_{seed % 1000:03d}{n:05d}"
        utr = _utr(rng, day)
        settled_at = datetime.combine(day, datetime.min.time()) + timedelta(hours=11)
        net = sum(m.net_paise for m in members)
        if net <= 0:                      # a batch that nets to zero never leaves
            settled_rows.extend(members)
            continue
        for m in members:
            settled_rows.append(PGTxn(**{**m.__dict__, "settlement_id": sid,
                                         "settlement_utr": utr, "settled_at": settled_at}))
            gt.payment_to_batch[m.entity_id] = sid
        batches.append({"id": sid, "utr": utr, "day": day, "net": net,
                        "members": [m.entity_id for m in members]})

    ds.pg = settled_rows + [p for p in payments if p.on_hold]
    rng.shuffle(ds.pg)

    # --- bank statement ------------------------------------------------------
    # Each event carries the batch ids it actually pays, so ground truth is
    # recorded from intent rather than re-derived by scanning narrations.
    b_pool = list(range(len(batches)))
    rng.shuffle(b_pool)
    b_take = lambda n: {b_pool.pop() for _ in range(min(n, len(b_pool)))}   # noqa: E731
    s_nobank = b_take(inj.missing_in_bank)
    s_amtbad = b_take(inj.settlement_amount_mismatch)
    s_split = b_take(inj.split_settlement)
    # A consolidated payout batches *consecutive* settlement days, so the pair
    # must be adjacent in time — a real bank never merges credits three weeks
    # apart, and generating that would only be testing a fantasy.
    merged_pairs: list[tuple[int, int]] = []
    free = sorted(b_pool)
    for a, b in zip(free, free[1:]):
        if len(merged_pairs) >= inj.merged_payout:
            break
        if b == a + 1 and all(a not in pr and b not in pr for pr in merged_pairs):
            merged_pairs.append((a, b))
    for pair in merged_pairs:
        for i in pair:
            b_pool.remove(i)
    s_merged = {i for pair in merged_pairs for i in pair}

    inj = replace(inj, missing_in_bank=len(s_nobank),
                  settlement_amount_mismatch=len(s_amtbad), split_settlement=len(s_split),
                  merged_payout=len(merged_pairs))

    events: list[tuple[date, str, str, int, list[str]]] = []
    direct_events: list[tuple[date, str, str, int, list[str], str]] = []
    for i, b in enumerate(batches):
        if i in s_nobank:
            gt.flag(b["id"], ExceptionCode.MISSING_IN_BANK,
                    f"Batch of {b['net']}p settled on {b['day']}; no bank credit found.")
            continue
        if i in s_merged:
            continue                      # emitted below, as part of a pair
        ifsc = f"{rng.choice(BANK_CODES)[:4]}000{rng.randrange(1000, 9999)}"
        narr = rng.choice(NARRATION_TEMPLATES).format(utr=b["utr"], ifsc=ifsc)
        if rng.random() < 0.25:
            narr = narr.lower()
        credit_day = b["day"] + timedelta(days=rng.choice([0, 0, 0, 1]))
        if i in s_split:
            first = round(b["net"] * rng.choice([0.4, 0.55, 0.7]))
            events.append((credit_day, narr, b["utr"][-8:], first, [b["id"]]))
            events.append((credit_day + timedelta(days=1),
                           narr.replace("SETTLEMENT", "SETTLEMENT PART 2"),
                           b["utr"][-8:], b["net"] - first, [b["id"]]))
            gt.flag(b["id"], ExceptionCode.SPLIT_SETTLEMENT,
                    "Batch arrived as two separate bank credits on consecutive days.")
        elif i in s_amtbad:
            delta = rng.choice([-1180, -590, 2500, -2360])   # bank charge / recovery
            events.append((credit_day, narr, b["utr"][-8:], b["net"] + delta, [b["id"]]))
            gt.flag(b["id"], ExceptionCode.SETTLEMENT_AMOUNT_MISMATCH,
                    f"Bank credited {b['net'] + delta}p against a batch of {b['net']}p.")
        else:
            events.append((credit_day, narr, b["utr"][-8:], b["net"], [b["id"]]))

    # Consolidated payouts: no UTR in the narration, so only amount-based
    # aggregate matching can recover these.
    for left, right in merged_pairs:
        bl, br = batches[left], batches[right]
        when = max(bl["day"], br["day"]) + timedelta(days=1)
        events.append((when, "NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT",
                       f"{rng.randrange(10**7, 10**8)}", bl["net"] + br["net"],
                       [bl["id"], br["id"]]))
        for b in (bl, br):
            gt.flag(b["id"], ExceptionCode.MERGED_PAYOUT,
                    "Paid to the bank inside a consolidated credit with no UTR reference.")

    # Customers who paid the bank directly. The invoice is real and the money
    # arrived; only the free-text narration ties them together. Where TDS was
    # withheld the amount does not even agree, so the link has to survive both a
    # mangled counterparty name and a short credit.
    for o, is_tds in direct_receipts:
        when = o["date"] + timedelta(days=rng.randrange(2, 9))
        gross = o["taxable"] + o["tax"]
        if is_tds:
            amount = gross - round(gross * 200 / 10_000)      # 2% u/s 194C
            payer = _abbreviate(o["customer"])
            narr = f"RTGS-{_utr(rng, when)}-{payer}-PMT AGST BILL"
            gt.flag(o["receipt"], ExceptionCode.TDS_WITHHELD,
                    f"Customer paid {amount}p against a {gross}p invoice, "
                    "withholding 2% TDS u/s 194C.")
        else:
            amount = gross
            narr = f"NEFT-{_utr(rng, when)}-{o['customer'].upper()}-DIRECT"
        direct_events.append((when, narr, f"{rng.randrange(10**7, 10**8)}", amount,
                              [], o["receipt"]))

    all_days = [b["day"] for b in batches] or [start]
    for n in range(inj.unexpected_bank_credit):
        when = rng.choice(all_days) + timedelta(days=rng.randrange(0, 3))
        amt = to_paise(f"{rng.randrange(1_00_000, 25_00_000) / 100:.2f}")
        narr, _ = NOISE_NARRATIONS[(4 + n) % len(NOISE_NARRATIONS)]
        events.append((when, narr, f"{rng.randrange(10**7, 10**8)}", amt, []))
    for n in range(12):                                   # ordinary business traffic
        when = rng.choice(all_days) + timedelta(days=rng.randrange(0, 4))
        narr, sign = NOISE_NARRATIONS[n % len(NOISE_NARRATIONS)]
        amt = sign * to_paise(f"{rng.randrange(80_000, 18_00_000) / 100:.2f}")
        events.append((when, narr, f"{rng.randrange(10**7, 10**8)}", amt, []))

    tagged = [(w, n, r, a, o, None) for (w, n, r, a, o) in events] + direct_events
    tagged.sort(key=lambda e: (e[0], e[1], e[3]))
    balance = to_paise("2450000.00")
    for n, (when, narr, ref, amt, owners, invoice) in enumerate(tagged, start=1):
        balance += amt
        line = BankLine(line_no=n, value_date=when, narration=narr,
                        ref_no=ref, amount_paise=amt, balance_paise=balance)
        ds.bank.append(line)
        for sid in owners:
            gt.batch_to_bank.setdefault(sid, []).append(line.key)
        if invoice:
            gt.invoice_to_bank[invoice] = line.key
        elif not owners and line.is_credit:
            gt.flag(line.key, ExceptionCode.UNEXPECTED_BANK_CREDIT,
                    "Credit in the bank with no settlement batch behind it.")

    # The exchange difference on every foreign invoice, computed from the same
    # rates the engine will be handed. Knowing the rate on both days means the
    # answer key can state what the revaluation must come to, rather than
    # trusting the engine's arithmetic to check itself.
    gt.rates = rates
    pay_by_receipt: dict[str, PGTxn] = {}
    for t in ds.pg:
        if t.type == "payment" and t.order_receipt:
            pay_by_receipt.setdefault(t.order_receipt, t)
    for inv in ds.invoices:
        if inv.currency == BASE or inv.invoice_no in gt.exceptions:
            continue
        p = pay_by_receipt.get(inv.invoice_no)
        if p is None:
            continue
        received = (p.settled_at or p.created_at).date()
        try:
            booked = rates.convert(Money(inv.gross_paise, inv.currency), BASE,
                                   inv.invoice_date)
            realised = rates.convert(Money(inv.gross_paise, inv.currency), BASE,
                                     received)
        except Exception:
            continue
        if realised.amount.minor != booked.amount.minor:
            gt.flag(inv.invoice_no, ExceptionCode.FX_REVALUATION,
                    f"{inv.currency} invoice booked at {booked.rate.rate}, "
                    f"received when the rate was {realised.rate.rate}.")

    return ds, gt, inj
# --------------------------------------------------------------------------- IO

def write(ds: Dataset, gt: GroundTruth, inj: Injections, out: Path, seed: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if gt.rates is not None:
        write_rates(gt.rates, out / "fx_rates.csv")

    with (out / "erp_invoices.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["invoice_no", "order_id", "customer", "invoice_date",
                    "taxable_amount", "tax_amount", "gross_amount", "currency"])
        for i in sorted(ds.invoices, key=lambda x: (x.invoice_date, x.invoice_no)):
            w.writerow([i.invoice_no, i.order_id, i.customer, i.invoice_date.isoformat(),
                        f"{i.taxable_paise / 100:.2f}", f"{i.tax_paise / 100:.2f}",
                        f"{i.gross_paise / 100:.2f}", i.currency])

    with (out / "pg_settlement_report.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_id", "type", "debit", "credit", "amount", "currency", "fee",
                    "tax", "on_hold", "settled", "created_at", "settled_at",
                    "settlement_id", "settlement_utr", "order_id", "order_receipt",
                    "method", "dispute_id", "parent_payment_id"])
        for t in sorted(ds.pg, key=lambda x: x.created_at):
            debit = f"{-t.amount_paise / 100:.2f}" if t.amount_paise < 0 else "0.00"
            credit = f"{t.amount_paise / 100:.2f}" if t.amount_paise > 0 else "0.00"
            w.writerow([t.entity_id, t.type, debit, credit, f"{t.amount_paise / 100:.2f}",
                        t.currency, f"{t.fee_paise / 100:.2f}",
                        f"{t.tax_paise / 100:.2f}",
                        "Y" if t.on_hold else "N", "Y" if t.settled else "N",
                        t.created_at.isoformat(sep=" "),
                        t.settled_at.isoformat(sep=" ") if t.settled_at else "",
                        t.settlement_id or "", t.settlement_utr or "", t.order_id or "",
                        t.order_receipt or "", t.method, t.dispute_id or "",
                        t.parent_payment_id or ""])

    with (out / "bank_statement.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["line_no", "value_date", "narration", "ref_no",
                    "debit", "credit", "balance"])
        for b in ds.bank:
            w.writerow([b.line_no, b.value_date.isoformat(), b.narration, b.ref_no,
                        f"{-b.amount_paise / 100:.2f}" if b.amount_paise < 0 else "",
                        f"{b.amount_paise / 100:.2f}" if b.amount_paise > 0 else "",
                        f"{b.balance_paise / 100:.2f}"])

    manifest = {
        "seed": seed,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": ds.counts(),
        "injected": {k: getattr(inj, k) for k in inj.__dataclass_fields__},
        "injected_total": inj.total(),
        "ground_truth_exceptions": len(gt.exceptions),
        "true_links": {
            "invoice_to_payment": sum(len(v) for v in gt.invoice_to_payment.values()),
            "payment_to_batch": len(gt.payment_to_batch),
            "batch_to_bank": sum(len(v) for v in gt.batch_to_bank.values()),
            "invoice_to_bank": len(gt.invoice_to_bank),
        },
        "fx_rates": len(gt.rates) if gt.rates is not None else 0,
        "currencies": sorted({i.currency for i in ds.invoices}),
        "unused": {
        },
    }
    (out / "ground_truth.json").write_text(json.dumps(
        {"manifest": manifest, **gt.to_json()}, indent=2))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
