# Reconciliation pack — synthetic period

Generated 2026-08-26 01:53. 355 source records across three systems, reconciled in 41.8 ms of engine time plus 23.7 s of model adjudication.

## Where the money is

| | Amount (INR) |
|---|---:|
| Captured at the gateway | +6,90,123.54 |
| Less funds on hold | -19,919.85 |
| Less captures not yet batched | +0.00 |
| **= Gross entering settlement** | **6,70,203.69** |
| Less refunds settled | -45,804.90 |
| Less gateway fees | -8,581.83 |
| Less GST on fees | -1,544.73 |
| Less dispute adjustments | -5,399.16 |
| **= Net settled into batches** | **6,08,873.07** |
| Batches say | **6,08,873.07** |
| Residual (must be zero) | +0.00 |
| Landed in the bank | +5,43,640.06 |
| Still in transit | +65,233.01 |
| Exchange gain / loss on foreign invoices | +0.00 |

Outside the settlement chain: **28,781.50** of invoices with no payment, **35,096.81** of payments with no invoice, **85,011.72** of bank credits nobody can place, and **358.65** of TDS withheld by customers.

## What reconciled

| Tier | How | Matches |
|---|---|---:|
| `T0_EXACT_ID` | identifier present on both sides | 153 |
| `T1_NORMALIZED_ID` | identifier matched after normalisation | 3 |
| `T2_AMOUNT_DATE` | no identifier; optimal amount + date assignment | 8 |
| `T3_AGGREGATE` | split or consolidated; matched as a group | 7 |
| `T4_ADJUDICATED` | model chose among candidates, arithmetic verified | 3 |
| | **total** | **174** |

## Measured against ground truth

The engine never reads `ground_truth.json`; only the evaluator does.

| Leg | Precision | Recall | F1 | True links |
|---|---:|---:|---:|---:|
| `invoice_to_payment` | 1.0000 | 1.0000 | 1.0000 | 126 |
| `settlement_to_bank` | 1.0000 | 1.0000 | 1.0000 | 49 |
| `invoice_to_bank` | 1.0000 | 1.0000 | 1.0000 | 6 |

Exception classification, scored strictly over (record, code) pairs: **precision 1.0000, recall 0.9412, F1 0.9697** on 68 true exceptions (64 correct, 0 false positives, 4 missed).

Throughput: **15 records/second** end to end including CSV parsing. Auto-clear rate **85.4%** (303 of 355 records correctly linked and carrying nothing that needs a human).

## Exceptions

64 findings. **47 need a human**, carrying **2,74,343.39** of exposure. 17 were explained and closed automatically.

### UNEXPECTED_BANK_CREDIT — 7 item(s), 85,011.72 (needs review)

*Money arrived in the bank that no settlement batch accounts for.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `bank:0017` | 21,561.47 | value_date=2026-07-14; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=33543500; amount=21561.47 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0022` | 19,507.56 | value_date=2026-07-17; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=79227966; amount=19507.56 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0040` | 13,124.04 | value_date=2026-07-29; narration=TERM LOAN DISBURSAL TL-99321; ref_no=76387789; amount=13124.04 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0059` | 11,056.29 | value_date=2026-08-10; narration=INT.PD:12345678:01-08-2026 TO 31-08-2026; ref_no=53408889; amount=11056.29 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0051` | 9,151.73 | value_date=2026-08-05; narration=TERM LOAN DISBURSAL TL-99321; ref_no=79215773; amount=9151.73 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0048` | 8,808.35 | value_date=2026-08-04; narration=CHQ PAID-004512; ref_no=60177567; amount=8808.35 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |
| `bank:0066` | 1,802.28 | value_date=2026-08-15; narration=NEFT-CITIN25081200099-ANAND TRADERS-DIRECT; ref_no=34896367; amount=1802.28 | Identify the payer before this is swept into the settlement control account; it is not gateway money on the evidence here. |

### MISSING_IN_BANK — 3 item(s), 65,233.01 (needs review)

*A settlement batch left the gateway but no matching bank credit has landed.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400015` | 43,233.74 | utr=SBIN0260716871951; settled_at=2026-07-16T11:00:00; net=43233.74; members=6 | Trace UTR SBIN0260716871951 with the bank. Until it lands, 43233.74 sits in gateway receivable, not in cash. |
| `setl_82400030` | 11,627.79 | utr=ICICN260729251568; settled_at=2026-07-29T11:00:00; net=11627.79; members=3 | Trace UTR ICICN260729251568 with the bank. Until it lands, 11627.79 sits in gateway receivable, not in cash. |
| `setl_82400008` | 10,371.48 | utr=ICICN260709431605; settled_at=2026-07-09T11:00:00; net=10371.48; members=5 | Trace UTR ICICN260709431605 with the bank. Until it lands, 10371.48 sits in gateway receivable, not in cash. |

### UNBILLED_PAYMENT — 5 item(s), 35,096.81 (needs review)

*A payment was captured with no invoice behind it — revenue recognised nowhere.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400071` | 10,818.10 | order_id=order_8240078; receipt=INV-2627-1079; captured_at=2026-08-03T07:51:00; method=netbanking | Raise an invoice for 10818.10 against order_8240078; revenue is currently unrecognised. |
| `pay_82400052` | 10,286.51 | order_id=order_8240059; receipt=INV-2627-1060; captured_at=2026-07-14T21:48:00; method=upi | Raise an invoice for 10286.51 against order_8240059; revenue is currently unrecognised. |
| `pay_82400037` | 8,576.37 | order_id=order_8240042; receipt=INV-2627-1043; captured_at=2026-07-31T12:55:00; method=upi | Raise an invoice for 8576.37 against order_8240042; revenue is currently unrecognised. |
| `pay_82400121` | 3,271.88 | order_id=order_8240128; receipt=INV-2627-1129; captured_at=2026-07-18T17:13:00; method=upi | Raise an invoice for 3271.88 against order_8240128; revenue is currently unrecognised. |
| `pay_82400110` | 2,143.95 | order_id=order_8240116; receipt=INV-2627-1117; captured_at=2026-07-09T16:23:00; method=upi | Raise an invoice for 2143.95 against order_8240116; revenue is currently unrecognised. |

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
| `pay_82400097` | 9,122.17 | order_id=order:order_8240102; first_capture=pay_82400096; first_at=2026-07-22T07:52:00; duplicate_at=2026-07-22T13:25:00 | Refund pay_82400097 and credit-note the order, or confirm two genuine shipments against order:order_8240102. |
| `pay_82400017` | 7,210.99 | order_id=order:order_8240017; first_capture=pay_82400016; first_at=2026-07-05T11:53:00; duplicate_at=2026-07-06T11:15:00 | Refund pay_82400017 and credit-note the order, or confirm two genuine shipments against order:order_8240017. |
| `pay_82400087` | 4,504.64 | order_id=order:order_8240092; first_capture=pay_82400086; first_at=2026-07-13T16:24:00; duplicate_at=2026-07-14T14:56:00 | Refund pay_82400087 and credit-note the order, or confirm two genuine shipments against order:order_8240092. |
| `pay_82400076` | 1,852.74 | order_id=order:order_8240082; first_capture=pay_82400075; first_at=2026-07-02T07:10:00; duplicate_at=2026-07-02T18:05:00 | Refund pay_82400076 and credit-note the order, or confirm two genuine shipments against order:order_8240082. |

### FUNDS_ON_HOLD — 4 item(s), 19,598.03 (needs review)

*A captured payment is held by the gateway and will not settle yet.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400115` | 8,033.51 | captured_at=2026-07-10T22:22:00; method=upi; amount=8033.51; order_id=order_8240122 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400039` | 6,962.93 | captured_at=2026-08-02T06:41:00; method=emi; amount=7218.46; order_id=order_8240044 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400079` | 3,054.81 | captured_at=2026-07-11T19:09:00; method=wallet; amount=3121.10; order_id=order_8240085 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |
| `pay_82400051` | 1,546.78 | captured_at=2026-07-18T17:10:00; method=upi; amount=1546.78; order_id=order_8240057 | Open a gateway ticket for the hold, and exclude this amount from the forward cash position until released. |

### ORPHAN_REFUND — 3 item(s), 7,485.64 (needs review)

*A refund exists whose original payment is not in the data.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `rfnd_82490000` | 3,238.47 | parent_payment_id=pay_82490000; amount=3238.47; created_at=2026-08-01T11:00:00 | Locate pay_82490000 — it is outside this report's period or was captured on another account. |
| `rfnd_82490002` | 2,895.63 | parent_payment_id=pay_82490002; amount=2895.63; created_at=2026-08-03T11:00:00 | Locate pay_82490002 — it is outside this report's period or was captured on another account. |
| `rfnd_82490001` | 1,351.54 | parent_payment_id=pay_82490001; amount=1351.54; created_at=2026-08-02T11:00:00 | Locate pay_82490001 — it is outside this report's period or was captured on another account. |

### CHARGEBACK_ADJUSTMENT — 3 item(s), 5,399.16 (needs review)

*The gateway debited a dispute or reserve adjustment the ERP does not carry.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `adjs_82400001` | 2,715.41 | dispute_id=disp_8240001; amount=2715.41; created_at=2026-08-07T09:30:00; settlement_id=setl_82400041 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_82400000` | 1,494.51 | dispute_id=disp_8240000; amount=1494.51; created_at=2026-08-03T09:30:00; settlement_id=setl_82400037 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |
| `adjs_82400002` | 1,189.24 | dispute_id=disp_8240002; amount=1189.24; created_at=2026-08-11T09:30:00; settlement_id=setl_82400044 | Book the dispute debit to a chargeback expense account and decide whether to contest before the representment window closes. |

### TAX_LINE_MISMATCH — 4 item(s), 2,723.36 (needs review)

*Invoice GST does not equal the statutory rate applied to the taxable value.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1128` | 1,197.63 | taxable=9212.53; gst_charged=460.63; gst_expected_18pct=1658.26; shortfall=1197.63 | Reconfirm the HSN rate on INV-2627-1128; if 18% applies, a revised invoice for 1197.63 of GST is due. |
| `INV-2627-1044` | 1,011.46 | taxable=7780.46; gst_charged=389.02; gst_expected_18pct=1400.48; shortfall=1011.46 | Reconfirm the HSN rate on INV-2627-1044; if 18% applies, a revised invoice for 1011.46 of GST is due. |
| `INV-2627-1084` | 345.57 | taxable=5759.56; gst_charged=691.15; gst_expected_18pct=1036.72; shortfall=345.57 | Reconfirm the HSN rate on INV-2627-1084; if 18% applies, a revised invoice for 345.57 of GST is due. |
| `INV-2627-1112` | 168.70 | taxable=1297.65; gst_charged=64.88; gst_expected_18pct=233.58; shortfall=168.70 | Reconfirm the HSN rate on INV-2627-1112; if 18% applies, a revised invoice for 168.70 of GST is due. |

### SHORT_PAYMENT — 3 item(s), 1,851.50 (needs review)

*The payment against an invoice is less than the invoice was raised for.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1100` | -1,098.90 | payment=pay_82400093; matched_on=identifier; invoice_gross=8453.05; paid=7354.15 | Sagar Marine paid 1098.90 less than INV-2627-1100. Chase the balance, or raise a credit note if it was agreed. |
| `INV-2627-1066` | -485.88 | payment=pay_82400058; matched_on=identifier; invoice_gross=9717.55; paid=9231.67 | Kaveri Seeds paid 485.88 less than INV-2627-1066. Chase the balance, or raise a credit note if it was agreed. |
| `INV-2627-1030` | -266.72 | payment=pay_82400027; matched_on=identifier; invoice_gross=2051.68; paid=1784.96 | Kaveri Seeds paid 266.72 less than INV-2627-1030. Chase the balance, or raise a credit note if it was agreed. |

### OVERPAYMENT — 2 item(s), 442.62 (needs review)

*More was paid against an invoice than it was raised for.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1110` | 379.95 | payment=pay_82400104; matched_on=identifier; invoice_gross=4221.70; paid=4601.65 | Gokul Dairy paid 379.95 more than INV-2627-1110. Refund it or hold it as an advance against the next invoice. |
| `INV-2627-1101` | 62.67 | payment=pay_82400094; matched_on=identifier; invoice_gross=1566.85; paid=1629.52 | Udupi Kitchens paid 62.67 more than INV-2627-1101. Refund it or hold it as an advance against the next invoice. |

### TDS_WITHHELD — 3 item(s), 358.65 (auto-resolved)

*A customer paid the invoice net of tax deducted at source.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1095` | 158.95 | relation=net_of_tds_2pct; bank_line=bank:0020; invoice_gross=7947.72; received=7788.77 | Book 158.95 as TDS receivable against Meridian Labs and collect Form 16A for the quarter. |
| `INV-2627-1020` | 131.32 | relation=net_of_tds_2pct; bank_line=bank:0046; invoice_gross=6566.17; received=6434.85 | Book 131.32 as TDS receivable against Everest Logistics and collect Form 16A for the quarter. |
| `INV-2627-1033` | 68.38 | relation=net_of_tds_2pct; bank_line=bank:0009; invoice_gross=3419.18; received=3350.80 | Book 68.38 as TDS receivable against Prabhat Printers and collect Form 16A for the quarter. |

### SETTLEMENT_AMOUNT_MISMATCH — 3 item(s), 29.50 (needs review)

*The bank credited a different amount than the settlement batch netted to.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400007` | -11.80 | utr=SBIN0260708365068; batch_net=44994.34; bank_credited=44982.54; delta=-11.80 | Bank credited less than the batch netted to, by an amount consistent with a correspondent charge; confirm with the bank. |
| `setl_82400041` | -11.80 | utr=AXISN260809179591; batch_net=5128.12; bank_credited=5116.32; delta=-11.80 | Bank credited less than the batch netted to, by an amount consistent with a correspondent charge; confirm with the bank. |
| `setl_82400023` | -5.90 | utr=AXISN260722597259; batch_net=1318.08; bank_credited=1312.18; delta=-5.90 | Bank credited less than the batch netted to, by an amount consistent with a correspondent charge; confirm with the bank. |

### FEE_VARIANCE — 5 item(s), 27.02 (auto-resolved)

*The gateway fee charged differs from the contracted MDR for that method.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `pay_82400122` | 8.85 | method=card; contracted_bps=200; expected_fee=86.88; charged_fee=94.38 | Recover 8.85 of MDR against pay_82400122. |
| `pay_82400029` | 8.85 | method=wallet; contracted_bps=180; expected_fee=151.26; charged_fee=158.76 | Recover 8.85 of MDR against pay_82400029. |
| `pay_82400004` | 4.84 | method=emi; contracted_bps=300; expected_fee=133.00; charged_fee=137.10 | Recover 4.84 of MDR against pay_82400004. |
| `pay_82400132` | 2.71 | method=card; contracted_bps=200; expected_fee=160.27; charged_fee=162.57 | Recover 2.71 of MDR against pay_82400132. |
| `pay_82400044` | -1.77 | method=netbanking; contracted_bps=150; expected_fee=157.30; charged_fee=155.80 | Credit back 1.77 of MDR against pay_82400044. |

### PART_PAYMENT — 3 item(s), 0.00 (auto-resolved)

*One invoice was settled by several captures that add up to it exactly.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `INV-2627-1022` | 0.00 | parts=2; payments=['pay_82400020', 'pay_82400020b']; dates=['2026-07-15', '2026-07-18']; sums_to=8117.89 | No action: the instalments reconcile exactly. Post them against the one invoice. |
| `INV-2627-1024` | 0.00 | parts=2; payments=['pay_82400022', 'pay_82400022b']; dates=['2026-07-16', '2026-07-20']; sums_to=2999.87 | No action: the instalments reconcile exactly. Post them against the one invoice. |
| `INV-2627-1056` | 0.00 | parts=2; payments=['pay_82400049', 'pay_82400049b']; dates=['2026-08-10', '2026-08-16']; sums_to=2104.14 | No action: the instalments reconcile exactly. Post them against the one invoice. |

### SPLIT_SETTLEMENT — 2 item(s), 0.00 (auto-resolved)

*One settlement batch arrived as more than one bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400019` | 0.00 | utr=KKBKN260720891036; parts=2; lines=['bank:0027', 'bank:0028']; dates=['2026-07-20', '2026-07-21'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |
| `setl_82400036` | 0.00 | utr=KKBKN260804644399; parts=2; lines=['bank:0047', 'bank:0050']; dates=['2026-08-04', '2026-08-05'] | No action: the parts reconcile exactly. Post as one receipt so the sub-ledger keeps a single settlement line. |

### MERGED_PAYOUT — 4 item(s), 0.00 (auto-resolved)

*Several settlement batches arrived as a single consolidated bank credit.*

| Record | Value (INR) | Evidence | Proposed action |
|---|---:|---|---|
| `setl_82400000` | 0.00 | bank_line=bank:0001; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=36059.63; this_batch=14605.69 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400001` | 0.00 | bank_line=bank:0001; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=36059.63; this_batch=21453.94 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400002` | 0.00 | bank_line=bank:0002; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=14847.25; this_batch=12.58 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |
| `setl_82400003` | 0.00 | bank_line=bank:0002; narration=NEFT CR RAZORPAY SOFTWARE PVT LTD CONSOLIDATED PAYOUT; credit_total=14847.25; this_batch=14834.67 | No action: the consolidated credit reconciles exactly. Ask the gateway to quote per-batch UTRs to avoid the search. |

## Model adjudications

Every residual the deterministic tiers could not settle, what the model proposed, and whether the arithmetic accepted it.

| Item | Candidates | Chose | Verdict | Model's reason |
|---|---|---|---|---|
| `setl_82400008` | 3 | `bank:0051` | rejected_by_arithmetic | The amounts (10371.48 vs 11056.29) and dates (2026-07-09 vs 2026-08-05/04) do not match any other transactions listed, s |
| `setl_82400015` | 3 | `bank:0017` | rejected_by_arithmetic | The settlement batch amount (INR 43233.74) matches the given amount in option A (21561.47), and both have the same date  |
| `setl_82400030` | 3 | `bank:0040` | rejected_by_arithmetic | The amounts (11627.79 vs 11056.29) and dates (2026-07-29 vs 2026-08-10) match between the given settlement batch and opt |
| `bank:0009` | 1 | `INV-2627-1033` | accepted | The narration mentions "BILL" which indicates it settles an unpaid invoice. |
| `bank:0020` | 1 | `INV-2627-1095` | accepted | The narration mentions "MERIDIAN LBS-PMT AGST BILL", which corresponds to Meridian Labs as the customer with an invoice  |
| `bank:0046` | 1 | `INV-2627-1020` | accepted | The narration mentions "EVEREST LGSTCS-PMT AGST BILL", which corresponds to an invoice for Everest Logistics with a gros |
