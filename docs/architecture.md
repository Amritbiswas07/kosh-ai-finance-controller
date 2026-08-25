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

| configuration | link F1 | exception F1 | LLM calls | LLM seconds |
|---|---:|---:|---:|---:|
| deterministic only | 0.8889 | 0.9280 | 0 | 0.0 |
| model on every leg | 1.0000 | 1.0000 | 18 | 47.5 |
| **model on the two legs it can help** | **1.0000** | **1.0000** | **6** | **21.2** |

Three of those six calls are spent on the settlement leg and every one is
rejected by the arithmetic gate — on *this* corpus there is nothing there to
find, exactly as the first ablation said. They are kept because the same call
is what recovers 4 of 4 links against statement formats the extractor cannot
parse (§8). The cost of carrying it is three wasted calls a run; the cost of
dropping it is every unfamiliar narration going unmatched.

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

**A stale answer key scored as a wrong answer.** Ground truth used to map an
invoice to a single payment id; instalments made it a list. The evaluator
iterated whatever it found, so an older file yielded one pair per *character* —
1,035 links from 135 invoices — and it reported link F1 0.0 for the leg without
a word of complaint. The 30-seed benchmark stayed at 1.0000 throughout, because
it regenerates its data every run and never touched the stale file. Two numbers
describing the same engine disagreed by half, and only comparing them exposed
it. The reader now normalises both shapes; the silence was the real defect.

**A benchmark that measured the same thing 30 times.** The first multi-seed run
returned identical scores to four decimal places on every seed. Not a bug — the
deterministic tiers never guess, so their accuracy is genuinely invariant — but
the generator injected a fixed *count* of each defect, so difficulty was
constant by construction. `Injections.jittered` now varies every defect rate per
seed, and the spread in the results is real.

---

## 8. Scored on data written against it

`generate.py` cannot escape one problem: the same person wrote the defects and
the detectors, so a perfect score there only proves the matcher can solve its
author's puzzle. `kosh.adversary` exists to be unfair. Its cases were chosen by
asking *what actually breaks a close* — not what Kosh can already do — and most
have no code in the taxonomy at all.

`scripts/adversarial.py` scores three different things, because they fail
differently:

| | deterministic | + model |
|---|---:|---:|
| links recovered | **1 / 4** | **4 / 4** |
| false links created | 1 / 8 | 1 / 8 |
| unknown breaks admitted as unknown | 1 / 3 | 1 / 3 |
| partial — true but unlinked | 2 / 3 | 2 / 3 |
| **invented a cause it could not know** | **0 %** | **0 %** |

The metric that matters is the last one. A finance tool that names a plausible
wrong cause is more dangerous than one that says nothing, and before the
`UNCLASSIFIED` fix it did exactly that — reporting a ₹4,200 *bank charge* on a
currency conversion. The two "partial" rows are not that failure: the engine
correctly reports *no credit arrived* and *this credit is unexplained*, and only
misses that they are the same event.

**This is also where the model stops being a garnish.** Three payouts of the
same amount on the same day — routine for subscription billing — with the
reference written in a statement format no pattern reads. Amount and date are
useless because they are identical; the reference is the only signal. Rules get
1 of 4. Reading the narration gets 4 of 4.

Two design changes came out of running it:

- **An optimal assignment is not an unambiguous one.** With identical costs the
  Hungarian pairing was decided by input order, so it "recovered" all three
  collisions by accident of ordering and, once shuffled, got them wrong while
  reporting them as matches. It now refuses ties, exactly as subset-sum already
  did.
- **Evidence has an order.** An identifier beats a reference in free text, which
  beats an amount that happens to agree. The blind amount pass had been running
  first and consuming lines whose reference was sitting there in plain sight.

An earlier ablation concluded model adjudication on the settlement leg was
worthless. It was — *on a corpus whose narrations all came from six templates
the extractor already knew*, where every residual genuinely had no counterparty.
The rule was never about the leg; it was about whether anything was there to
find. Against unfamiliar formats the same call earns its place.

---

## 9. State, and why reconciliation is not a report

Everything above is stateless: point it at three files and it says what it
found. Real reconciliation is not like that, and the reason is timing. A
settlement sent on Monday reaches the bank on Wednesday, so **an exception
raised today is routinely answered by data that does not exist yet**. A tool
that starts from nothing every morning cannot tell you that yesterday's break
has cleared — which is the one thing a controller most wants to know.

`kosh.store` keeps three things between runs, in SQLite:

- **records** — a content fingerprint of every source row, so re-loading the
  same export is a no-op rather than a double count
- **links** — matches already made, and the run that made them
- **exceptions** — an open/resolved lifecycle with an age

`kosh sync` reconciles the current snapshot and folds the result into that
ledger. `scripts/live_demo.py` runs three days of it:

```
─── Day 1 · settlements sent, money still in flight ───
  343 records, 343 new, 166 new links
    opened   setl_82400041   MISSING_IN_BANK   23,733.74
    …and 56 more opened

─── Day 2 · the bank statement catches up ───
  347 records, 4 new or amended, 4 new links
    CLEARED  setl_82400041   MISSING_IN_BANK   23,733.74  (matched once the data arrived)
    53 still open, oldest 1 run(s) old

─── Day 3 · the same file is loaded twice ───
  347 records, 0 new or amended, 0 new links
    nothing changed
    53 still open, oldest 2 run(s) old
```

Day 2 is the point: **the engine did not change between those runs.** The data
caught up, the break answered itself, and the ledger recorded that it had. Day 3
is the quiet one, and the reason fingerprints exist — a reconciliation you can
safely re-run is a reconciliation somebody can automate.

**The matching engine still knows nothing about any of this.** Reconciliation
runs over the whole current snapshot and the store diffs one run against the
last, which keeps hidden state out of the part that gets audited. A test asserts
the engine never reads the store.

---

## 10. From ledger to control

Keeping state told you a break had cleared. It did not let anyone *work* one, and
a reconciliation nobody can act on inside the tool is still a report.

- **Assignment.** An exception gets an owner and moves `open → investigating`.
- **Maker–checker.** Closing a break above ₹10,000 needs a second name, and it
  cannot be the same name. Below it, one signature — demanding two for a ₹23
  bank charge is how controls get routed around rather than followed.
- **`written_off` is a decision, not a disappearance.** The money is still gone;
  somebody chose to stop chasing it, and their name is on that.
- **An audit trail.** Every assign, note, resolution and confirmation records
  who, when and why.
- **A decision outlives the run.** What a person closed is not reopened by the
  next reconciliation.

**And a feedback loop.** When a controller links two records the engine could
not, `kosh exception link` records it, and every later run replays it at a tier
above every other — because a person decided, and asking again each morning is
not diligence, it is the tool forgetting. The confirmations are *passed into*
`reconcile()` rather than read by it, so the engine keeps no hidden state and
the call that used them shows exactly which ones applied.

```
$ kosh exception link --key setl_82400020 --to bank:0004 --by amrit \
      --note "gateway confirmed by email"
$ kosh sync
  replaying 1 link(s) a person already confirmed
    - bank:0004      UNEXPECTED_BANK_CREDIT   8,936.54   matched once the data arrived
    - setl_82400020  MISSING_IN_BANK          1,645.52   matched once the data arrived
```

---

## 11. Reading what banks actually send

The bank side was a CSV of my own devising, which is a weak claim: a parser that
only reads its author's format has not met reality. `kosh.feeds` reads **MT940**,
the SWIFT statement format Indian banks export from corporate net-banking, and
it is awkward in ways a hand-rolled CSV never is — comma decimals, two-digit
years, dates split across two fields, narrations continued across lines, and a
debit/credit marker that is a letter inside a field rather than a column. `RC`
and `RD` reverse a credit and a debit respectively, so the sign flips.

Drop a `bank_statement.sta` beside the other files and it is used instead of the
CSV; nothing else changes. The parser also checks the statement against **its
own opening and closing balances** — one that does not add up has been truncated
or edited, which is worth knowing before reconciling any of it against anything
else.

---

## 12. Known limitations

- **Single currency.** Everything is INR paise. Multi-currency settlement would
  need an FX rate table and a revaluation line in the bridge; neither exists.
- **No fuzzy string matching on counterparty names.** Leg D uses token overlap
  and a model, not edit distance or embeddings. `ANND TRDRS` (first word also
  mangled) would defeat both.
- **T3 caps merges at three batches.** A gateway consolidating a week of payouts
  into one credit would not be found.
- **The corpus is synthetic.** It was written to be hard in the ways real data
  is hard, but it was written by the same person as the matcher. The multi-seed
  benchmark reduces that risk and the adversarial corpus (§8) attacks it
  directly; neither eliminates it.
- **Ingest is pull, not push.** The gateway comes from its API and the bank from
  an MT940 download, but both are fetched on demand; there is no webhook
  listener, so nothing reacts the moment a payment is captured. The ERP side is
  still a CSV, because there is no single ERP format to read.
- **The API has never run against a live key.** I have no merchant credentials.
  The Authorization header, paging, every error branch and the whole mapping are
  exercised through an injected transport, so the only untested thing is the
  socket itself — but that is still untested.
- **The model is small.** Qwen2.5-1.5B is reliable at choosing among candidates
  and unreliable at prose (§7). A larger local model would likely widen what
  T4 can recover; it has not been tried.
- **One false link survives**, on the adversarial corpus: when a bank reuses a
  single reference across two payouts, the engine matches the credit to one of
  them rather than declining. Reported rather than fixed.
- **Two breaks are seen but not connected.** A capture and its exact reversal,
  and two batches netted against a chargeback, are each reported truthfully as
  separate absences. Linking them needs a pass that reasons over combinations of
  *signed* amounts, which does not exist.
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
