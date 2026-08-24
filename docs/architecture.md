# Kosh — architecture

A three-way settlement reconciliation agent. This document explains what it
does, why it is built the way it is, and where it fails.

---

## 1. The problem, stated precisely

A merchant on a payment gateway has three systems that should agree and never
quite do:

| System | What it believes |
|---|---|
| **ERP / accounting** | what was sold, invoiced and taxed |
| **Gateway settlement report** | what was captured, refunded, charged in fees, and paid out |
| **Bank statement** | what actually arrived in the current account |

Between a customer's card being charged and the money being spendable there are
five places it can go missing: a fee that differs from the contracted MDR, a
refund the ERP never saw, a batch the bank has not credited yet, a dispute the
gateway silently debited, and a customer who paid the bank directly and never
told anyone. A finance controller closing the month has to explain every one of
them, and be able to show their working to an auditor.

Track 4 asks for the match rate **and the exceptions the agent could not
resolve**. This build treats the second half as the harder and more important
one.

---

## 2. Shape of the system

```
  erp_invoices.csv ─┐
  pg_settlement…csv ─┼─► ingest ─► batches ─┐
  bank_statement.csv ┘   (tolerant,         │
                          errors surfaced)  │
                                            ▼
      ┌─────────────────────────────────────────────────────────────┐
      │  Leg A  ERP invoice   ↔ gateway payment                      │
      │  Leg B  gateway + ERP internal integrity  (no counterparty)  │
      │  Leg C  settlement batch ↔ bank credit                       │
      │  Leg D  unpaid invoice ↔ unexplained bank credit             │
      └─────────────────────────────────────────────────────────────┘
                    │                          │
              matches (tiered)           findings (typed)
                    │                          │
                    ▼                          ▼
              cash position  ◄──────────  exception list
                    │                          │
                    └────────► report ◄────────┘
                        (md · html · json)

              ground_truth.json ──► evaluate ──► metrics
              (opened by the evaluator, and by nothing else)
```

Leg D runs last, on purpose: it consumes the exceptions the first three legs
produced. An invoice that looks unpaid and a bank credit that looks unexplained
are frequently the same settled transaction seen from two sides.

---

## 3. The matching cascade

Five tiers, each strictly more permissive than the last, each running only on
what the tier above could not resolve.

| Tier | Basis | Typical share |
|---|---|---|
| `T0_EXACT_ID` | `order_id` or UTR present on both sides | ~91% |
| `T1_NORMALIZED_ID` | same identifier after case/punctuation normalisation | ~2% |
| `T2_AMOUNT_DATE` | no identifier; globally optimal 1:1 assignment | ~5% |
| `T3_AGGREGATE` | split credits (1:N) and consolidated payouts (N:1) | ~2% |
| `T4_ADJUDICATED` | a local model chooses among fixed candidates | ~1% |

**T2 uses the Hungarian algorithm, not greedy nearest-neighbour.** Greedy
matching lets an early row claim a partner a later row needed, so the output
depends on the order rows happen to appear in the file. `scipy`'s
`linear_sum_assignment` minimises total cost across the whole set, which makes
the result stable under row shuffling — asserted directly in
`test_assignment_is_stable_under_row_shuffling`.

**T3 subset-sum is deliberately bounded.** Given enough rows, subset-sum will
always find *some* combination that adds up, and a coincidental sum posted as a
match is worse than an unmatched line. So: only credits whose narration claims
to be gateway money, only batches inside the date window, at most three
batches, exact sums, and — if two candidate subsets tie — no match at all.

---

## 4. Where the model is, and where it deliberately is not

**The model never decides whether two records match.** Matching is arithmetic
and identifier logic; it is the part an auditor will examine, and "the model
said so" is not an audit trail.

The model is asked exactly one question, on one leg: *a customer paid the bank
directly, the amount is right but the narration has mangled their name past a
token-overlap threshold — which open invoice is this?* Reading `MERIDIAN LBS`
as Meridian Labs is genuinely beyond a Jaccard threshold and genuinely within a
1.5B model's reach.

Three constraints hold it in place:

1. **Fixed candidate set.** The model receives labelled options and can return
   only one of them, or NONE. It cannot name a record that was not offered.
2. **Arithmetic disposes.** Every pick is re-checked: the chosen invoice's gross
   must equal the credit, or equal it net of a statutory TDS rate (1%, 2%, 10%).
   Picks that fail are logged as `rejected_by_arithmetic` and discarded.
3. **A literal name token is required.** The model may only choose among
   candidates that share at least one word with the narration (see §7).

Confidence for a model-adjudicated match is capped below 0.8 and the tier is
stamped on the match, so a reviewer can filter the report down to exactly the
rows a model touched.

### Why not the other legs?

It was wired into all four legs first, then measured — `scripts/ablation.py`:

| configuration | link F1 | exc F1 | LLM calls | LLM seconds |
|---|---:|---:|---:|---:|
| deterministic only | 0.8889 | 0.9217 | 0 | 0.0 |
| model on every leg | 1.0000 | 1.0000 | 18 | 46.4 |
| **model on invoice→bank only** | **1.0000** | **1.0000** | **3** | **9.3** |

Identical accuracy from 3 calls instead of 18. On legs A and C the residual is
records with no counterparty *in the data at all* — an invoice nobody paid, a
batch the bank has not sent. There is nothing for a model to find there, so
every answer it gives is a false positive; of its 15 calls on those legs, 13
were declines and 2 were wrong picks that the arithmetic gate caught. The
default is therefore `DEFAULT_ADJUDICATED_LEGS = {INVOICE_BANK}`, on evidence.

---

## 5. Measurement

`data/ground_truth.json` records every true link and every true exception. It is
written by the generator and read by `kosh.evaluate` — and by nothing else. The
separation is enforced behaviourally, not by convention: a test deletes the file
and asserts the run produces byte-identical matches and findings.

Two different questions are scored separately.

- **Linking** — precision/recall/F1 over unordered pairs, per leg.
- **Classification** — scored strictly over `(record, code)` pairs, so the right
  label on the wrong row costs a false positive *and* a false negative rather
  than cancelling out.

`auto_clear_rate` is deliberately conservative: a record counts as cleared only
if it was correctly linked **and** carries no finding that needs a human.
Exceptions the engine explained but could not resolve are not counted as wins.

### The exception taxonomy is closed

Fourteen codes plus `UNCLASSIFIED`. An open-ended "other" bucket lets an engine
hide failures in prose; if a residual does not fit a code, that is a gap in the
taxonomy and should be visible.

`MISSING_IN_BANK · UNEXPECTED_BANK_CREDIT · SETTLEMENT_AMOUNT_MISMATCH ·
FEE_VARIANCE · DUPLICATE_PAYMENT · UNBILLED_PAYMENT · UNPAID_INVOICE ·
ORPHAN_REFUND · FUNDS_ON_HOLD · TAX_LINE_MISMATCH · SPLIT_SETTLEMENT ·
CHARGEBACK_ADJUSTMENT · MERGED_PAYOUT · TDS_WITHHELD`

---

## 6. The cash position

Reconciling records is half the job; the other half is *where is the money*.
`kosh.position` builds an explicit roll-forward with an identity that must hold:

```
gross entering settlement − refunds − fees − GST − disputes  =  Σ batch nets
```

`residual` stays on the face of the report rather than being plugged. It earned
its place immediately: the first version netted gateway fees against captures
that were on hold and had therefore never entered a batch, producing a ₹6,057
break that looked like a real reconciliation failure. It was the function
double-counting. A subtotal would have hidden it.

---

## 7. Things that went wrong

Recorded because the failures are more informative than the passes.

**The evaluator caught a bug in the data generator.** Duplicate-payment
detection kept scoring 2 false positives and 2 false negatives. The engine flags
the *later* capture as the duplicate — standard controller policy — but the
generator assigned each payment a random hour, so the row it *called* the
duplicate sometimes had the earlier timestamp. Ground truth was not decidable
from the data. Fixed in the generator; a test now asserts a duplicate always
follows its original.

**`CHOICE: NONE` was parsed as candidate N.** The regex was
`CHOICE:\s*([A-Z]|NONE)`; alternation is ordered, `[A-Z]` matched the `N`, and
every decline was recorded as "model named an out-of-range option". It failed
safe — an out-of-range index returns no match — so accuracy was never affected,
but the adjudication log was lying about what the model said.

**The model linked a bank interest credit to a customer invoice.** On benchmark
seed 13, `INT.PD:12345678:01-08-2026 TO 31-08-2026` — the bank paying itself —
landed within 2% of an open invoice, so the TDS arithmetic gate passed by
coincidence. The model's own stated reason identified it as an interest payment
and it linked it anyway. The fix is deterministic, not a better prompt: a
candidate must share at least one literal word of the customer's name with the
narration. Bank-generated narrations never do.

**The macro link score reported a collapse that never happened.** The stress
sweep appeared to show link F1 falling from 0.88 to 0.22 as defect density rose
— an alarming curve, and entirely an artefact. `PRF.f1` is 0.0 when there is
nothing to find, and the macro was averaging those empty legs in. At high defect
density the generator exhausts its order pool, so the `invoice_to_bank` leg had
*zero* true links; scoring that as a failure invented the collapse. The macro
now averages only legs with non-zero support, and the corrected sweep shows
accuracy flat-to-rising while the auto-clear rate falls — which is the honest
result. Had I published the first curve it would have been a real misstatement,
and the only thing that caught it was the number `0.6667` being suspiciously
exactly two-thirds.

**Two date overflows found by turning the density up.** `datetime(2026, 8, 3 + n
* 4, ...)` is fine for three adjustments and raises `day is out of range for
month` for twelve. Day-of-month arithmetic instead of `timedelta`, in two
places, both only reachable at defect rates the default corpus never hits.

**The same model is reliable at choosing and unreliable at explaining.** Asked
*which* invoice a credit settles — a constrained choice among fixed candidates,
verified afterwards by arithmetic — Qwen2.5-1.5B was correct on every one of the
90 adjudications across the 30-seed benchmark. Asked to *explain* a finding in
prose, the same model, on the same run, produced: a total it invented by adding
four on-hold balances that are excluded from the figure it was explaining; a
dollar sign on a rupee amount; and a confident causal claim that a batch was
"partially credited by the bank" when the finding was that no credit arrived at
all. Grounded numbers, invented reasoning.

Hence `kosh ask` defaults to `--llm off`, and with the model on, three guards
apply: every amount in the answer must already appear in the retrieved facts, an
aggregation vocabulary (`total`, `combined`, `sum of`) is rejected outright, and
a rejected answer is replaced by the underlying records rather than reworded.
The deterministic path already carries a correct explanation for every code —
`EXCEPTION_MEANING` plus the finding's `proposed_action` — so on this evidence
the model adds risk to Q&A without adding information. It stays as an opt-in.

**A benchmark that measured the same thing 30 times.** The first multi-seed run
returned identical scores to four decimal places on every seed. Not a bug — the
deterministic tiers never guess, so their accuracy is genuinely invariant — but
the generator injected a fixed *count* of each defect, so difficulty was
constant by construction. `Injections.jittered` now varies every defect rate per
seed, and the spread in the results is real.

---

## 8. Known limitations

- **Single currency.** Everything is INR paise. Multi-currency settlement would
  need an FX rate table and a revaluation line in the bridge; neither exists.
- **No fuzzy string matching on counterparty names.** Leg D uses token overlap
  and a model, not edit distance or embeddings. `ANND TRDRS` (first word also
  mangled) would defeat both.
- **T3 caps merges at three batches.** A gateway consolidating a week of payouts
  into one credit would not be found.
- **The corpus is synthetic.** It was written to be hard in the ways real data
  is hard, but it was written by the same person as the matcher. The multi-seed
  benchmark reduces that risk; it does not eliminate it.
- **`UNCLASSIFIED` has never fired.** Good, but it means that path is untested
  against a real unknown.
- **I could not find a defect density that makes it mis-match.** Scaling every
  defect rate to 6× left precision and recall at 1.0 while the auto-clear rate
  fell to 39.5%. That is the expected behaviour of a cascade that never guesses,
  but "I could not break it" is weaker evidence than "here is where it breaks",
  and the honest reading is that **defect density is the wrong stress axis**.
  What would genuinely stress it is defect *ambiguity* — many invoices sharing
  an amount and a date, counterparty names mangled in the first word as well as
  the last. That corpus is not written.
- **The 30-seed model run scores 1.0000 on every seed.** Reported because it is
  what happened, but a perfect score across a corpus written by the same author
  as the matcher is a statement about the corpus at least as much as about the
  engine.
