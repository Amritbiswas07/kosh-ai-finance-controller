# Reconciliation pack — synthetic

Generated 2026-08-25 04:02. 345 source records across three systems, reconciled in 22.0 ms of engine time plus 10.1 s of model adjudication.

## Where the money is

| | Amount (INR) |
|---|---:|
| Captured at the gateway | +7,26,213.94 |
| Less funds on hold | -19,919.85 |
| Less captures not yet batched | +0.00 |
| **= Gross entering settlement** | **7,06,294.09** |
| Less refunds settled | -40,711.41 |
| Less gateway fees | -8,967.77 |
| Less GST on fees | -1,614.19 |
| Less dispute adjustments | -3,751.34 |
| **= Net settled into batches** | **6,51,249.38** |
| Batches say | **6,51,249.38** |
| Residual (must be zero) | +0.00 |
| Landed in the bank | +5,79,299.00 |
| Still in transit | +71,950.38 |

Outside the settlement chain: **28,781.50** of invoices with no payment, **35,096.81** of payments with no invoice, **49,706.81** of bank credits nobody can place, and **358.65** of TDS withheld by customers.

## What reconciled

| Tier | How | Matches |
|---|---|---:|
| `T0_EXACT_ID` | identifier present on both sides | 149 |
| `T1_NORMALIZED_ID` | identifier matched after normalisation | 3 |
| `T2_AMOUNT_DATE` | no identifier; optimal amount + date assignment | 8 |
| `T3_AGGREGATE` | split or consolidated; matched as a group | 4 |
| `T4_ADJUDICATED` | model chose among candidates, arithmetic verified | 3 |
| | **total** | **167** |

## Measured against ground truth

The engine never reads `ground_truth.json`; only the evaluator does.

| Leg | Precision | Recall | F1 | True links |
|---|---:|---:|---:|---:|
| `invoice_to_payment` | 1.0000 | 1.0000 | 1.0000 | 123 |
| `settlement_to_bank` | 1.0000 | 1.0000 | 1.0000 | 42 |
| `invoice_to_bank` | 1.0000 | 1.0000 | 1.0000 | 6 |

Exception classification, scored strictly over (record, code) pairs: **precision 1.0000, recall 1.0000, F1 1.0000** on 56 true exceptions (56 correct, 0 false positives, 0 missed).

Throughput: **34 records/second** end to end including CSV parsing. Auto-clear rate **86.1%** (297 of 345 records correctly linked and carrying nothing that needs a human).

## Exceptions

56 findings. **42 need a human**, carrying **2,43,717.18** of exposure. 14 were explained and closed automatically.

### MISSING_IN_BANK — 3 item(s), 71,950.38 (needs review)

*A settlement batch left the gateway but no matching bank credit has landed.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400005` | 44,994.34 | utr=KKBKN260708563337; settled_at=2026-07-08T11:00:00; net=44994.34; members=7 | Trace UTR KKBKN260708563337 with the bank. Until it lands, 44994.34 sits in gateway receivable, not in cash. |
| `setl_82400027` | 19,645.23 | utr=ICICN260730729956; settled_at=2026-07-30T11:00:00; net=19645.23; members=4 | Trace UTR ICICN260730729956 with the bank. Until it lands, 19645.23 sits in gateway receivable, not in cash. |
| `setl_82400028` | 7,310.81 | utr=SBIN0260731871951; settled_at=2026-07-31T11:00:00; net=7310.81; members=1 | Trace UTR SBIN0260731871951 with the bank. Until it lands, 7310.81 sits in gateway receivable, not in cash. |

### UNEXPECTED_BANK_CREDIT — 7 item(s), 49,706.81 (needs review)

*Money arrived in the bank that no settlement batch accounts for.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `bank:0059` | 9,684.65 | value_date=2026-08-13; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=74133746; amount=9684.65 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0009` | 9,667.90 | value_date=2026-07-12; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=52005675; amount=9667.90 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0049` | 6,876.63 | value_date=2026-08-07; narration=TERM LOAN DISBURSAL TL-99321; ref_no=66728391; amount=6876.63 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0058` | 6,488.54 | value_date=2026-08-13; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=45093259; amount=6488.54 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0032` | 5,929.95 | value_date=2026-07-27; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=86892032; amount=5929.95 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0056` | 5,819.78 | value_date=2026-08-12; narration=TERM LOAN DISBURSAL TL-99321; ref_no=65404711; amount=5819.78 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0050` | 5,239.36 | value_date=2026-08-08; narration=CHQ PAID-004512; ref_no=70647722; amount=5239.36 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |

### UNBILLED_PAYMENT — 5 item(s), 35,096.81 (needs review)

*A payment was captured with no invoice behind it — revenue recognised nowhere.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400071` | 10,818.10 | order_id=order_8240078; receipt=INV-2627-1079; captured_at=2026-08-03T12:33:00; method=netbanking | Raise an invoice for 10818.10 against order_8240078; revenue is currently unrecognised. |
| `pay_82400052` | 10,286.51 | order_id=order_8240059; receipt=INV-2627-1060; captured_at=2026-07-14T11:24:00; method=upi | Raise an invoice for 10286.51 against order_8240059; revenue is currently unrecognised. |
| `pay_82400037` | 8,576.37 | order_id=order_8240042; receipt=INV-2627-1043; captured_at=2026-07-31T13:26:00; method=upi | Raise an invoice for 8576.37 against order_8240042; revenue is currently unrecognised. |
| `pay_82400121` | 3,271.88 | order_id=order_8240128; receipt=INV-2627-1129; captured_at=2026-07-18T16:23:00; method=upi | Raise an invoice for 3271.88 against order_8240128; revenue is currently unrecognised. |
| `pay_82400110` | 2,143.95 | order_id=order_8240116; receipt=INV-2627-1117; captured_at=2026-07-09T15:47:00; method=upi | Raise an invoice for 2143.95 against order_8240116; revenue is currently unrecognised. |

### UNPAID_INVOICE — 6 item(s), 28,781.50 (needs review)

*An invoice was raised but no payment was ever captured against it.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1054` | 7,967.86 | order_id=order_8240053; customer=Quantum Fasteners; invoice_date=2026-08-06; gross=7967.86 | Chase Quantum Fasteners for 7967.86 against INV-2627-1054, or write it off if the order was cancelled. |
| `INV-2627-1034` | 6,441.23 | order_id=order_8240033; customer=Orion Sportswear; invoice_date=2026-08-11; gross=6441.23 | Chase Orion Sportswear for 6441.23 against INV-2627-1034, or write it off if the order was cancelled. |
| `INV-2627-1059` | 6,188.11 | order_id=order_8240058; customer=Trident Cables; invoice_date=2026-08-02; gross=6188.11 | Chase Trident Cables for 6188.11 against INV-2627-1059, or write it off if the order was cancelled. |
| `INV-2627-1001` | 4,464.05 | order_id=order_8240000; customer=Vindhya Ceramics; invoice_date=2026-07-30; gross=4464.05 | Chase Vindhya Ceramics for 4464.05 against INV-2627-1001, or write it off if the order was cancelled. |
| `INV-2627-1025` | 2,423.30 | order_id=order_8240024; customer=Quantum Fasteners; invoice_date=2026-07-11; gross=2423.30 | Chase Quantum Fasteners for 2423.30 against INV-2627-1025, or write it off if the order was cancelled. |
| `INV-2627-1116` | 1,296.95 | order_id=order_8240115; customer=Prabhat Printers; invoice_date=2026-07-26; gross=1296.95 | Chase Prabhat Printers for 1296.95 against INV-2627-1116, or write it off if the order was cancelled. |

### DUPLICATE_PAYMENT — 4 item(s), 22,690.54 (needs review)

*One order was paid for more than once.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400097` | 9,122.17 | order_id=order_8240102; first_capture=pay_82400096; first_at=2026-07-22T10:41:00; duplicate_at=2026-07-22T18:44:00 | Refund pay_82400097 and credit-note the order, or confirm two genuine shipments against order_8240102. |
| `pay_82400017` | 7,210.99 | order_id=order_8240017; first_capture=pay_82400016; first_at=2026-07-05T11:53:00; duplicate_at=2026-07-06T11:15:00 | Refund pay_82400017 and credit-note the order, or confirm two genuine shipments against order_8240017. |
| `pay_82400087` | 4,504.64 | order_id=order_8240092; first_capture=pay_82400086; first_at=2026-07-13T17:15:00; duplicate_at=2026-07-14T07:25:00 | Refund pay_82400087 and credit-note the order, or confirm two genuine shipments against order_8240092. |
| `pay_82400076` | 1,852.74 | order_id=order_8240082; first_capture=pay_82400075; first_at=2026-07-02T14:19:00; duplicate_at=2026-07-02T22:28:00 | Refund pay_82400076 and credit-note the order, or confirm two genuine shipments against order_8240082. |

### FUNDS_ON_HOLD — 4 item(s), 19,598.03 (needs review)

*A captured payment is held by the gateway and will not settle yet.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400115` | 8,033.51 | captured_at=2026-07-10T13:46:00; method=upi; amount=8033.51; order_id=order_8240122 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400039` | 6,962.93 | captured_at=2026-08-02T18:19:00; method=emi; amount=7218.46; order_id=order_8240044 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400079` | 3,054.81 | captured_at=2026-07-11T11:24:00; method=wallet; amount=3121.10; order_id=order_8240085 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400051` | 1,546.78 | captured_at=2026-07-18T07:56:00; method=upi; amount=1546.78; order_id=order_8240057 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |

### ORPHAN_REFUND — 3 item(s), 9,388.17 (needs review)

*A refund exists whose original payment is not in the data.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `rfnd_82490002` | 3,891.25 | parent_payment_id=pay_82490002; amount=3891.25; created_at=2026-08-03T11:00:00 | Locate pay_82490002 — it is outside this report's period or was captured on another account. |
| `rfnd_82490000` | 3,423.06 | parent_payment_id=pay_82490000; amount=3423.06; created_at=2026-08-01T11:00:00 | Locate pay_82490000 — it is outside this report's period or was captured on another account. |
| `rfnd_82490001` | 2,073.86 | parent_payment_id=pay_82490001; amount=2073.86; created_at=2026-08-02T11:00:00 | Locate pay_82490001 — it is outside this report's period or was captured on another account. |

### CHARGEBACK_ADJUSTMENT — 3 item(s), 3,751.34 (needs review)

*The gateway debited a dispute or reserve adjustment the ERP does not carry.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `adjs_82400000` | 1,432.52 | dispute_id=disp_8240000; amount=1432.52; created_at=2026-08-03T09:30:00; settlement_id=setl_82400033 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_82400002` | 1,267.48 | dispute_id=disp_8240002; amount=1267.48; created_at=2026-08-11T09:30:00; settlement_id=setl_82400040 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_82400001` | 1,051.34 | dispute_id=disp_8240001; amount=1051.34; created_at=2026-08-07T09:30:00; settlement_id=setl_82400037 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |

### TAX_LINE_MISMATCH — 4 item(s), 2,681.40 (needs review)

*Invoice GST does not equal the statutory rate applied to the taxable value.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1128` | 1,197.63 | taxable=9212.53; gst_charged=460.63; gst_expected_18pct=1658.26; shortfall=1197.63 | Reconfirm the HSN rate on INV-2627-1128; if 18% applies, a revised invoice for 1197.63 of GST is due. |
| `INV-2627-1044` | -778.05 | taxable=7780.46; gst_charged=2178.53; gst_expected_18pct=1400.48; shortfall=-778.05 | Reconfirm the HSN rate on INV-2627-1044; if 18% applies, 778.05 of GST was over-collected and needs a credit note. |
| `INV-2627-1084` | -575.96 | taxable=5759.56; gst_charged=1612.68; gst_expected_18pct=1036.72; shortfall=-575.96 | Reconfirm the HSN rate on INV-2627-1084; if 18% applies, 575.96 of GST was over-collected and needs a credit note. |
| `INV-2627-1112` | -129.76 | taxable=1297.65; gst_charged=363.34; gst_expected_18pct=233.58; shortfall=-129.76 | Reconfirm the HSN rate on INV-2627-1112; if 18% applies, 129.76 of GST was over-collected and needs a credit note. |

### TDS_WITHHELD — 3 item(s), 358.65 (auto-resolved)

*A customer paid the invoice net of tax deducted at source.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1095` | 158.95 | relation=net_of_tds_2pct; bank_line=bank:0013; invoice_gross=7947.72; received=7788.77 | Book 158.95 as TDS receivable against Meridian Labs and collect Form 16A for the quarter. |
| `INV-2627-1020` | 131.32 | relation=net_of_tds_2pct; bank_line=bank:0036; invoice_gross=6566.17; received=6434.85 | Book 131.32 as TDS receivable against Everest Logistics and collect Form 16A for the quarter. |
| `INV-2627-1033` | 68.38 | relation=net_of_tds_2pct; bank_line=bank:0008; invoice_gross=3419.18; received=3350.80 | Book 68.38 as TDS receivable against Prabhat Printers and collect Form 16A for the quarter. |

### SETTLEMENT_AMOUNT_MISMATCH — 3 item(s), 72.20 (needs review)

*The bank credited a different amount than the settlement batch netted to.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400034` | 25.00 | utr=AXISN260806485862; batch_net=14270.59; bank_credited=14295.59; delta=25.00 | Bank credited more than the batch netted to; check for a prior shortfall being made good. |
| `setl_82400017` | -23.60 | utr=KKBKN260720982508; batch_net=16992.68; bank_credited=16969.08; delta=-23.60 | Bank credited less than the batch netted to; check for a correspondent charge or a recovery. |
| `setl_82400026` | -23.60 | utr=HDFCN260729758214; batch_net=7401.27; bank_credited=7377.67; delta=-23.60 | Bank credited less than the batch netted to; check for a correspondent charge or a recovery. |

### FEE_VARIANCE — 5 item(s), 23.96 (auto-resolved)

*The gateway fee charged differs from the contracted MDR for that method.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400029` | 8.85 | method=wallet; contracted_bps=180; expected_fee=151.26; charged_fee=158.76 | Recover 8.85 of MDR against pay_82400029. |
| `pay_82400122` | 4.84 | method=card; contracted_bps=200; expected_fee=86.88; charged_fee=90.98 | Recover 4.84 of MDR against pay_82400122. |
| `pay_82400004` | 4.84 | method=emi; contracted_bps=300; expected_fee=133.00; charged_fee=137.10 | Recover 4.84 of MDR against pay_82400004. |
| `pay_82400044` | 2.72 | method=netbanking; contracted_bps=150; expected_fee=157.30; charged_fee=159.60 | Recover 2.72 of MDR against pay_82400044. |
| `pay_82400132` | 2.71 | method=card; contracted_bps=200; expected_fee=160.27; charged_fee=162.57 | Recover 2.71 of MDR against pay_82400132. |

### SPLIT_SETTLEMENT — 2 item(s), 0.00 (auto-resolved)

*One settlement batch arrived as more than one bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400009` | 0.00 | utr=AXISN260712707695; parts=2; lines=['bank:0010', 'bank:0012']; dates=['2026-07-12', '2026-07-13'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |
| `setl_82400016` | 0.00 | utr=ICICN260719844352; parts=2; lines=['bank:0022', 'bank:0024']; dates=['2026-07-20', '2026-07-21'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |

### MERGED_PAYOUT — 4 item(s), 0.00 (auto-resolved)

*Several settlement batches arrived as a single consolidated bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400000` | 0.00 | bank_line=bank:0001; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=37247.54; this_batch=14605.69 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400001` | 0.00 | bank_line=bank:0001; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=37247.54; this_batch=22641.85 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400002` | 0.00 | bank_line=bank:0002; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=40400.47; this_batch=14834.67 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400003` | 0.00 | bank_line=bank:0002; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=40400.47; this_batch=25565.80 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |

## Model adjudications

Every residual the deterministic tiers could not settle, what the model proposed, and whether the arithmetic accepted it.

| Item | Candidates | Chose | Verdict | Model's reason |
|---|---|---|---|---|
| `bank:0008` | 1 | `INV-2627-1033` | accepted | The narration mentions "BILL" which indicates it settles an unpaid invoice. |
| `bank:0013` | 1 | `INV-2627-1095` | accepted | The narration mentions "MERIDIAN LBS-PMT AGST BILL", which corresponds to Meridian Labs as the customer with an invoice  |
| `bank:0036` | 1 | `INV-2627-1020` | accepted | The narration mentions "EVEREST LGSTCS-PMT AGST BILL", which corresponds to an invoice from Everest Logistics with a gro |
