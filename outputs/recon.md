# Reconciliation pack — synthetic

Generated 2026-08-25 03:17. 341 source records across three systems, reconciled in 37.1 ms of engine time plus 8.1 s of model adjudication.

## Where the money is

| | Amount (INR) |
|---|---:|
| Captured at the gateway | +7,47,795.67 |
| Less funds on hold | -25,351.09 |
| Less captures not yet batched | -3,895.56 |
| **= Gross entering settlement** | **7,18,549.02** |
| Less refunds settled | -38,823.33 |
| Less gateway fees | -10,173.86 |
| Less GST on fees | -1,831.28 |
| Less dispute adjustments | -5,956.56 |
| **= Net settled into batches** | **6,61,763.99** |
| Batches say | **6,61,763.99** |
| Residual (must be zero) | +0.00 |
| Landed in the bank | +6,24,301.10 |
| Still in transit | +37,462.89 |

Outside the settlement chain: **36,271.78** of invoices with no payment, **27,330.74** of payments with no invoice, **59,135.52** of bank credits nobody can place, and **200.87** of TDS withheld by customers.

## What reconciled

| Tier | How | Matches |
|---|---|---:|
| `T0_EXACT_ID` | identifier present on both sides | 145 |
| `T1_NORMALIZED_ID` | identifier matched after normalisation | 3 |
| `T2_AMOUNT_DATE` | no identifier; optimal amount + date assignment | 8 |
| `T3_AGGREGATE` | split or consolidated; matched as a group | 4 |
| `T4_ADJUDICATED` | model chose among candidates, arithmetic verified | 3 |
| | **total** | **163** |

## Measured against ground truth

The engine never reads `ground_truth.json`; only the evaluator does.

| Leg | Precision | Recall | F1 | True links |
|---|---:|---:|---:|---:|
| `invoice_to_payment` | 1.0000 | 1.0000 | 1.0000 | 123 |
| `settlement_to_bank` | 1.0000 | 1.0000 | 1.0000 | 38 |
| `invoice_to_bank` | 1.0000 | 1.0000 | 1.0000 | 6 |

Exception classification, scored strictly over (record, code) pairs: **precision 1.0000, recall 1.0000, F1 1.0000** on 56 true exceptions (56 correct, 0 false positives, 0 missed).

Throughput: **42 records/second** end to end including CSV parsing. Auto-clear rate **85.9%** (293 of 341 records correctly linked and carrying nothing that needs a human).

## Exceptions

56 findings. **42 need a human**, carrying **2,11,662.92** of exposure. 14 were explained and closed automatically.

### UNEXPECTED_BANK_CREDIT — 7 item(s), 59,135.52 (needs review)

*Money arrived in the bank that no settlement batch accounts for.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `bank:0020` | 15,738.19 | value_date=2026-07-18; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=66071364; amount=15738.19 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0007` | 11,544.08 | value_date=2026-07-10; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=89038264; amount=11544.08 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0024` | 9,166.57 | value_date=2026-07-22; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=54863973; amount=9166.57 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0010` | 8,292.75 | value_date=2026-07-10; narration=TERM LOAN DISBURSAL TL-99321; ref_no=66590700; amount=8292.75 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0038` | 6,576.34 | value_date=2026-08-02; narration=TERM LOAN DISBURSAL TL-99321; ref_no=88960597; amount=6576.34 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0017` | 6,059.78 | value_date=2026-07-16; narration=CHQ PAID-004512; ref_no=62007486; amount=6059.78 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0018` | 1,757.81 | value_date=2026-07-16; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=21060049; amount=1757.81 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |

### MISSING_IN_BANK — 3 item(s), 37,462.89 (needs review)

*A settlement batch left the gateway but no matching bank credit has landed.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_07700021` | 19,950.87 | utr=HDFCN260725928268; settled_at=2026-07-25T11:00:00; net=19950.87; members=3 | Trace UTR HDFCN260725928268 with the bank. Until it lands, 19950.87 sits in gateway receivable, not in cash. |
| `setl_07700018` | 13,539.93 | utr=ICICN260721944757; settled_at=2026-07-21T11:00:00; net=13539.93; members=2 | Trace UTR ICICN260721944757 with the bank. Until it lands, 13539.93 sits in gateway receivable, not in cash. |
| `setl_07700014` | 3,972.09 | utr=SBIN0260717346157; settled_at=2026-07-17T11:00:00; net=3972.09; members=1 | Trace UTR SBIN0260717346157 with the bank. Until it lands, 3972.09 sits in gateway receivable, not in cash. |

### UNPAID_INVOICE — 6 item(s), 36,271.78 (needs review)

*An invoice was raised but no payment was ever captured against it.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1102` | 9,436.24 | order_id=order_0770101; customer=Udupi Kitchens; invoice_date=2026-07-13; gross=9436.24 | Chase Udupi Kitchens for 9436.24 against INV-2627-1102, or write it off if the order was cancelled. |
| `INV-2627-1055` | 8,908.88 | order_id=order_0770054; customer=Sagar Marine; invoice_date=2026-08-08; gross=8908.88 | Chase Sagar Marine for 8908.88 against INV-2627-1055, or write it off if the order was cancelled. |
| `INV-2627-1034` | 7,664.86 | order_id=order_0770033; customer=Windward Sails; invoice_date=2026-07-25; gross=7664.86 | Chase Windward Sails for 7664.86 against INV-2627-1034, or write it off if the order was cancelled. |
| `INV-2627-1112` | 5,060.80 | order_id=order_0770111; customer=Udupi Kitchens; invoice_date=2026-07-13; gross=5060.80 | Chase Udupi Kitchens for 5060.80 against INV-2627-1112, or write it off if the order was cancelled. |
| `INV-2627-1099` | 2,671.43 | order_id=order_0770098; customer=Kaveri Seeds; invoice_date=2026-07-09; gross=2671.43 | Chase Kaveri Seeds for 2671.43 against INV-2627-1099, or write it off if the order was cancelled. |
| `INV-2627-1032` | 2,529.57 | order_id=order_0770031; customer=Frontier Optics; invoice_date=2026-07-06; gross=2529.57 | Chase Frontier Optics for 2529.57 against INV-2627-1032, or write it off if the order was cancelled. |

### UNBILLED_PAYMENT — 5 item(s), 27,330.74 (needs review)

*A payment was captured with no invoice behind it — revenue recognised nowhere.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_07700126` | 9,879.04 | order_id=order_0770133; receipt=INV-2627-1134; captured_at=2026-07-18T21:20:00; method=upi | Raise an invoice for 9879.04 against order_0770133; revenue is currently unrecognised. |
| `pay_07700077` | 8,823.13 | order_id=order_0770079; receipt=INV-2627-1080; captured_at=2026-07-23T07:47:00; method=card | Raise an invoice for 8823.13 against order_0770079; revenue is currently unrecognised. |
| `pay_07700084` | 5,481.08 | order_id=order_0770086; receipt=INV-2627-1087; captured_at=2026-08-14T16:56:00; method=emi | Raise an invoice for 5481.08 against order_0770086; revenue is currently unrecognised. |
| `pay_07700027` | 2,342.87 | order_id=order_0770027; receipt=INV-2627-1028; captured_at=2026-07-31T11:21:00; method=upi | Raise an invoice for 2342.87 against order_0770027; revenue is currently unrecognised. |
| `pay_07700052` | 804.62 | order_id=order_0770055; receipt=INV-2627-1056; captured_at=2026-08-03T06:03:00; method=upi | Raise an invoice for 804.62 against order_0770055; revenue is currently unrecognised. |

### FUNDS_ON_HOLD — 4 item(s), 24,410.30 (needs review)

*A captured payment is held by the gateway and will not settle yet.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_07700017` | 9,418.25 | captured_at=2026-07-26T22:37:00; method=amex; amount=9823.98; order_id=order_0770016 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_07700014` | 5,980.36 | captured_at=2026-08-06T20:43:00; method=amex; amount=6237.99; order_id=order_0770013 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_07700085` | 4,757.92 | captured_at=2026-08-04T15:03:00; method=emi; amount=4932.54; order_id=order_0770087 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_07700048` | 4,253.77 | captured_at=2026-08-12T21:30:00; method=card; amount=4356.58; order_id=order_0770050 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |

### DUPLICATE_PAYMENT — 4 item(s), 17,544.41 (needs review)

*One order was paid for more than once.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_07700115` | 7,422.38 | order_id=order_0770119; first_capture=pay_07700114; first_at=2026-07-02T20:11:00; duplicate_at=2026-07-03T07:46:00 | Refund pay_07700115 and credit-note the order, or confirm two genuine shipments against order_0770119. |
| `pay_07700032` | 6,796.66 | order_id=order_0770034; first_capture=pay_07700031; first_at=2026-08-01T19:08:00; duplicate_at=2026-08-02T21:34:00 | Refund pay_07700032 and credit-note the order, or confirm two genuine shipments against order_0770034. |
| `pay_07700067` | 2,737.69 | order_id=order_0770069; first_capture=pay_07700066; first_at=2026-07-05T20:28:00; duplicate_at=2026-07-06T23:31:00 | Refund pay_07700067 and credit-note the order, or confirm two genuine shipments against order_0770069. |
| `pay_07700061` | 587.68 | order_id=order_0770063; first_capture=pay_07700060; first_at=2026-07-28T08:07:00; duplicate_at=2026-07-29T03:15:00 | Refund pay_07700061 and credit-note the order, or confirm two genuine shipments against order_0770063. |

### CHARGEBACK_ADJUSTMENT — 3 item(s), 5,956.56 (needs review)

*The gateway debited a dispute or reserve adjustment the ERP does not carry.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `adjs_07700001` | 2,743.32 | dispute_id=disp_0770001; amount=2743.32; created_at=2026-08-07T09:30:00; settlement_id=setl_07700035 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_07700000` | 2,375.67 | dispute_id=disp_0770000; amount=2375.67; created_at=2026-08-03T09:30:00; settlement_id=setl_07700031 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_07700002` | 837.57 | dispute_id=disp_0770002; amount=837.57; created_at=2026-08-11T09:30:00; settlement_id=setl_07700037 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |

### ORPHAN_REFUND — 3 item(s), 2,476.92 (needs review)

*A refund exists whose original payment is not in the data.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `rfnd_07790002` | 1,138.48 | parent_payment_id=pay_07790002; amount=1138.48; created_at=2026-08-03T11:00:00 | Locate pay_07790002 — it is outside this report's period or was captured on another account. |
| `rfnd_07790000` | 1,060.09 | parent_payment_id=pay_07790000; amount=1060.09; created_at=2026-08-01T11:00:00 | Locate pay_07790000 — it is outside this report's period or was captured on another account. |
| `rfnd_07790001` | 278.35 | parent_payment_id=pay_07790001; amount=278.35; created_at=2026-08-02T11:00:00 | Locate pay_07790001 — it is outside this report's period or was captured on another account. |

### TAX_LINE_MISMATCH — 4 item(s), 1,013.40 (needs review)

*Invoice GST does not equal the statutory rate applied to the taxable value.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1119` | 370.74 | taxable=2851.84; gst_charged=142.59; gst_expected_18pct=513.33; shortfall=370.74 | Reconfirm the HSN rate on INV-2627-1119; if 18% applies, a revised invoice for 370.74 of GST is due. |
| `INV-2627-1018` | 357.25 | taxable=2748.05; gst_charged=137.40; gst_expected_18pct=494.65; shortfall=357.25 | Reconfirm the HSN rate on INV-2627-1018; if 18% applies, a revised invoice for 357.25 of GST is due. |
| `INV-2627-1075` | -146.10 | taxable=1461.04; gst_charged=409.09; gst_expected_18pct=262.99; shortfall=-146.10 | Reconfirm the HSN rate on INV-2627-1075; if 18% applies, 146.10 of GST was over-collected and needs a credit note. |
| `INV-2627-1040` | -139.31 | taxable=1393.09; gst_charged=390.07; gst_expected_18pct=250.76; shortfall=-139.31 | Reconfirm the HSN rate on INV-2627-1040; if 18% applies, 139.31 of GST was over-collected and needs a credit note. |

### TDS_WITHHELD — 3 item(s), 200.87 (auto-resolved)

*A customer paid the invoice net of tax deducted at source.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1129` | 149.22 | relation=net_of_tds_2pct; bank_line=bank:0027; invoice_gross=7461.12; received=7311.90 | Book 149.22 as TDS receivable against Orion Sportswear and collect Form 16A for the quarter. |
| `INV-2627-1068` | 32.35 | relation=net_of_tds_2pct; bank_line=bank:0055; invoice_gross=1617.31; received=1584.96 | Book 32.35 as TDS receivable against Chetna Organics and collect Form 16A for the quarter. |
| `INV-2627-1020` | 19.30 | relation=net_of_tds_2pct; bank_line=bank:0001; invoice_gross=965.25; received=945.95 | Book 19.30 as TDS receivable against Bharat Textiles and collect Form 16A for the quarter. |

### SETTLEMENT_AMOUNT_MISMATCH — 3 item(s), 60.40 (needs review)

*The bank credited a different amount than the settlement batch netted to.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_07700026` | 25.00 | utr=ICICN260730182432; batch_net=15952.89; bank_credited=15977.89; delta=25.00 | Bank credited more than the batch netted to; check for a prior shortfall being made good. |
| `setl_07700031` | -23.60 | utr=SBIN0260805228371; batch_net=3224.82; bank_credited=3201.22; delta=-23.60 | Bank credited less than the batch netted to; check for a correspondent charge or a recovery. |
| `setl_07700030` | -11.80 | utr=KKBKN260804389918; batch_net=27427.59; bank_credited=27415.79; delta=-11.80 | Bank credited less than the batch netted to; check for a correspondent charge or a recovery. |

### FEE_VARIANCE — 5 item(s), 22.66 (auto-resolved)

*The gateway fee charged differs from the contracted MDR for that method.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_07700015` | 8.85 | method=amex; contracted_bps=350; expected_fee=136.34; charged_fee=143.84 | Recover 8.85 of MDR against pay_07700015. |
| `pay_07700083` | 4.84 | method=wallet; contracted_bps=180; expected_fee=126.47; charged_fee=130.57 | Recover 4.84 of MDR against pay_07700083. |
| `pay_07700004` | -3.60 | method=amex; contracted_bps=350; expected_fee=259.32; charged_fee=256.27 | Credit back 3.60 of MDR against pay_07700004. |
| `pay_07700063` | -3.60 | method=card; contracted_bps=200; expected_fee=210.42; charged_fee=207.37 | Credit back 3.60 of MDR against pay_07700063. |
| `pay_07700096` | -1.77 | method=upi; contracted_bps=0; expected_fee=0.00; charged_fee=-1.50 | Credit back 1.77 of MDR against pay_07700096. |

### SPLIT_SETTLEMENT — 2 item(s), 0.00 (auto-resolved)

*One settlement batch arrived as more than one bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_07700002` | 0.00 | utr=AXISN260705545424; parts=2; lines=['bank:0002', 'bank:0004']; dates=['2026-07-05', '2026-07-06'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |
| `setl_07700009` | 0.00 | utr=KKBKN260712452884; parts=2; lines=['bank:0012', 'bank:0014']; dates=['2026-07-12', '2026-07-13'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |

### MERGED_PAYOUT — 4 item(s), 0.00 (auto-resolved)

*Several settlement batches arrived as a single consolidated bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_07700000` | 0.00 | bank_line=bank:0003; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=43801.25; this_batch=15138.85 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_07700001` | 0.00 | bank_line=bank:0003; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=43801.25; this_batch=28662.40 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_07700003` | 0.00 | bank_line=bank:0005; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=17906.90; this_batch=12569.30 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_07700004` | 0.00 | bank_line=bank:0005; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=17906.90; this_batch=5337.60 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |

## Model adjudications

Every residual the deterministic tiers could not settle, what the model proposed, and whether the arithmetic accepted it.

| Item | Candidates | Chose | Verdict | Model's reason |
|---|---|---|---|---|
| `bank:0001` | 1 | `INV-2627-1020` | accepted | The narration mentions "BHARAT TXTLS-PMT AGST BILL", which indicates an invoice from Bharat Textiles for payment of a bi |
| `bank:0027` | 1 | `INV-2627-1129` | accepted | The narration mentions "BILL" which indicates it settles an invoice, matching the customer and invoice date provided. |
| `bank:0055` | 1 | `INV-2627-1068` | accepted | The narration mentions "BILL" which indicates it settles an unpaid invoice. The amount (INR 1584.96) matches the given u |
