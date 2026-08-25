"""A corpus written against the engine, not for it.

`generate.py` has a problem it cannot solve: the same person wrote the defects
and the detectors, so a perfect score only proves the matcher can solve its
author's puzzle. This module exists to be unfair. Every case here is a real
reconciliation failure mode, and most of them have no code in the taxonomy at
all — they were chosen by asking "what actually breaks a close?", not "what can
Kosh already do?".

Each case declares what an honest engine should do, and the categories matter:

  ``link``     a true link exists and should be found
  ``no_link``  no link exists; creating one is a false positive
  ``unknown``  a real break the taxonomy has no code for. The only acceptable
               answers are UNCLASSIFIED or silence. A confident specific label
               here is a **confabulation** — the engine inventing a cause — and
               that is the number worth reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .schema import Dataset, Invoice, PGTxn, BankLine

TOL = 100


@dataclass
class Case:
    name: str
    kind: str                      # link | no_link | unknown
    why: str
    links: list[tuple[str, str]] = field(default_factory=list)   # (batch, bank key)
    keys: list[str] = field(default_factory=list)                # records under test


def _inv(no, order, taxable, day=1, cust="Acme Industries"):
    tax = round(taxable * 1800 / 10_000)
    return Invoice(no, order, cust, date(2026, 7, day), taxable, tax, taxable + tax)


def _settled(eid, amount, sid, utr, day=1, settle_day=3, method="card"):
    """A captured, settled payment. `net_paise` is what the bank should receive —
    gross less the fee and the GST on it — and every credit below is built from
    that, not from the gross. Building them from the gross made the harness itself
    wrong: the engine was correctly reporting a mismatch that the test had
    created."""
    fee = round(amount * 200 / 10_000)
    return PGTxn(eid, "payment", amount, fee, round(fee * 1800 / 10_000),
                 datetime(2026, 7, day, 10), method, order_id=f"o_{eid}",
                 settlement_id=sid, settlement_utr=utr,
                 settled_at=datetime(2026, 7, settle_day, 11))


def build() -> tuple[Dataset, list[Case]]:
    inv: list[Invoice] = []
    pg: list[PGTxn] = []
    bank: list[BankLine] = []
    cases: list[Case] = []
    n = [0]

    def credit(day, narration, amount, ref="0"):
        n[0] += 1
        line = BankLine(n[0], date(2026, 7, day), narration, ref, amount, 0)
        bank.append(line)
        return line

    # --- 1. statement formats no pattern was written for ---------------------
    # Real banks change templates without notice. The reference is present and
    # a person can see it; the extractor cannot.
    # All three settle the same amount on the same day — routine for subscription
    # billing — so amount and date cannot tell them apart. The reference is the
    # only signal, and it is written in a format no pattern reads. This is the
    # case that isolates what a model can do and a regular expression cannot.
    unseen = [
        ("BY TRANSFER-NEFT*HDFC*260703551001*RAZORPAY SOFTWARE PVT LTD", 4_10_000_00),
        ("CR|UPI|260703551002|RAZORPAYSOFT|SETTLEMENT", 4_10_000_00),
        ("TRF FRM RAZORPAY SOFTWARE  REF 260703551003  VALUE 03JUL26", 4_10_000_00),
    ]
    # The credits are emitted in reverse order so that an assignment on amount
    # and date alone — every cost identical — cannot land on the right pairing by
    # accident of ordering. Getting these right requires reading the reference.
    un_txns = []
    for i, (narr, amt) in enumerate(unseen):
        t = _settled(f"pay_un{i}", amt, f"setl_un{i}", f"HDFCN26070355100{i + 1}")
        pg.append(t)
        un_txns.append((i, narr, t))
    for i, narr, t in reversed(un_txns):
        line = credit(3, narr, t.net_paise)
        cases.append(Case(f"unseen_narration_{i}", "link",
                          "three payouts of the same amount on the same day; the "
                          "reference is the only signal and no pattern reads it",
                          links=[(t.settlement_id, line.key)],
                          keys=[t.settlement_id, line.key]))

    # --- 2. settlement credited after a currency conversion ------------------
    t_fx = _settled("pay_fx", 8_00_000_00, "setl_fx", "ICICN260703661001")
    pg.append(t_fx)
    credit(3, "NEFT-ICICN260703661001-RAZORPAY-SETTLEMENT",
           round(t_fx.net_paise * 0.9647))           # 3.53% lost to spread
    cases.append(Case("fx_conversion", "unknown",
                      "credit is short by an FX spread; no code exists for this",
                      keys=["setl_fx"]))

    # --- 3. a payment and its exact reversal ---------------------------------
    rev = 1_50_000_00
    pg.append(_settled("pay_rev", rev, "setl_rev", "SBIN0260704771001", settle_day=4))
    pg.append(PGTxn("rfnd_rev", "refund", -rev, 0, 0, datetime(2026, 7, 4, 12), "card",
                    order_id="o_pay_rev", parent_payment_id="pay_rev",
                    settlement_id="setl_rev", settlement_utr="SBIN0260704771001",
                    settled_at=datetime(2026, 7, 4, 11)))
    cases.append(Case("reversal_pair", "unknown",
                      "capture and exact reversal net to a fee-only settlement",
                      keys=["setl_rev"]))

    # --- 4. two batches netted against a chargeback in one credit ------------
    t_na = _settled("pay_na", 3_00_000_00, "setl_na", "AXISN260705881001", settle_day=5)
    t_nb = _settled("pay_nb", 2_00_000_00, "setl_nb", "AXISN260705881002", settle_day=5)
    pg += [t_na, t_nb]
    netline = credit(5, "NEFT CR RAZORPAY SOFTWARE PVT LTD NET SETTLEMENT",
                     t_na.net_paise + t_nb.net_paise - 45_000_00)
    cases.append(Case("netted_payout", "unknown",
                      "one credit pays two batches less a chargeback; subset-sum "
                      "cannot reach it because of the deduction",
                      keys=["setl_na", "setl_nb", netline.key]))

    # --- 5. identical amounts, identical day, no order references ------------
    # The classic way an assignment matcher quietly pairs the wrong rows.
    same = 99_000_00
    for i in range(6):
        inv.append(_inv(f"INV-C{i}", f"oc{i}", same, day=8,
                        cust=f"Collision Traders {i}"))
        pg.append(PGTxn(f"pay_c{i}", "payment", inv[-1].gross_paise,
                        round(inv[-1].gross_paise * 200 / 10_000),
                        round(round(inv[-1].gross_paise * 200 / 10_000) * 1800 / 10_000),
                        datetime(2026, 7, 8, 9 + i), "card"))       # no order_id
        cases.append(Case(f"amount_collision_{i}", "no_link",
                          "six invoices and six payments share an amount and a day "
                          "with nothing to tell them apart",
                          keys=[f"INV-C{i}", f"pay_c{i}"]))

    # --- 6. the statement export contains the same credit twice --------------
    t_dup = _settled("pay_dup", 5_50_000_00, "setl_dup", "KKBKN260710991001",
                     day=10, settle_day=12)
    pg.append(t_dup)
    d1 = credit(12, "NEFT-KKBKN260710991001-RAZORPAY-SETTLEMENT", t_dup.net_paise)
    d2 = credit(12, "NEFT-KKBKN260710991001-RAZORPAY-SETTLEMENT", t_dup.net_paise)
    cases.append(Case("duplicate_bank_line", "no_link",
                      "one payout appears twice in the export; matching both "
                      "double-counts the cash",
                      links=[("setl_dup", d1.key)], keys=["setl_dup", d2.key]))

    # --- 7. the bank reuses one reference for two different payouts ----------
    ru = [_settled(f"pay_ru{i}", amt, f"setl_ru{i}", "ICICN260714aa1001",
                   day=14, settle_day=16)
          for i, amt in enumerate((1_20_000_00, 1_60_000_00))]
    pg += ru
    r1 = credit(16, "NEFT-ICICN260714AA1001-RAZORPAY-SETTLEMENT", ru[0].net_paise)
    cases.append(Case("utr_reuse", "no_link",
                      "one reference on two batches; only one credit exists",
                      keys=["setl_ru0", "setl_ru1", r1.key]))

    # --- 8. a one-paise rounding drift ---------------------------------------
    t_rp = _settled("pay_rp", 7_77_777_00, "setl_rp", "SBIN0260718221001",
                    day=18, settle_day=20)
    pg.append(t_rp)
    rline = credit(20, "NEFT-SBIN0260718221001-RAZORPAY-SETTLEMENT", t_rp.net_paise - 1)
    cases.append(Case("one_paise_drift", "link",
                      "off by a single paise; must still reconcile",
                      links=[("setl_rp", rline.key)], keys=["setl_rp"]))

    return Dataset(invoices=inv, pg=pg, bank=bank), cases
