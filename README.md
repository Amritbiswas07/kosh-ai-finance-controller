# Kosh — an AI finance controller

**Razorpay AI Buildathon · Track 4 — "Run the books and the cash position."**

Kosh reconciles three systems that should agree and never do — an ERP invoice
register, a payment-gateway settlement report, and a bank statement — then
reports what matched, where the cash actually is, and, at full length, every
exception it could not resolve.

It runs entirely on this machine. The one model it uses is a 1.5B open-weights
model loaded from a local cache with the network disabled. No API key, no
hosted inference, no per-token cost.

---

## Quick start

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt && ./.venv/bin/pip install -e .
```

```bash
./.venv/bin/kosh generate --seed 20260824
```

```bash
./.venv/bin/kosh recon
```

That runs the whole reconciliation deterministically — no model, no downloads —
and writes `outputs/recon.md`, `recon.html` and `recon.json`.

To enable the adjudication tier (downloads ~3 GB of weights once, then offline
forever):

```bash
./.venv/bin/pip install -r requirements-llm.txt && ./.venv/bin/kosh recon --llm on
```

| Command | What it does |
|---|---|
| `kosh generate --seed N` | Write a synthetic three-source corpus **and its ground truth** |
| `kosh recon [--llm on]` | Reconcile; write the pack as Markdown, HTML and JSON |
| `kosh evaluate` | Print precision/recall/F1 against ground truth as JSON |
| `kosh ask "…" [--llm on]` | Question the completed run (settlement Q&A) |
| `pytest -q` | 97 tests, no model loaded, ~0.2 s |
| `python scripts/benchmark.py --seeds 30 [--llm]` | Accuracy across 30 regenerated worlds |
| `python scripts/ablation.py` | Does the model earn its place, and on which leg? |
| `python scripts/verify_offline.py` | Run everything with all outbound sockets blocked |

---

## What Track 4 asked for, and where it is

| The ask | Where |
|---|---|
| One financial-operations workflow | Three-way settlement reconciliation, four legs |
| 50+ synthetic data records | **345** per run, across three sources — 10,277 across the benchmark |
| Report match accuracy | Precision/recall/F1 per leg, scored against held-out ground truth |
| Report unresolved exceptions | Every one, itemised, with evidence, exposure and a proposed action |
| Throughput plus measured accuracy | See below — both, measured, not estimated |
| Multiple matches, not cherry-picked | 30 regenerated corpora with varying defect rates; distribution reported |

---

## Results

**345 records · 44 seconds of nothing but arithmetic.** The deterministic engine
reconciles a full corpus in **1.2 ms** — about **71,000 records/second** end to
end including CSV parsing. With the model enabled, wall time is dominated
entirely by the three adjudication calls.

Across **30 regenerated corpora (10,277 records)**, each with independently
jittered defect rates:

| configuration | link F1 (mean) | link F1 (min) | exc P | exc R | exc F1 | auto-clear | records/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| deterministic only | 0.8898 | 0.7778 | 0.9004 | 0.9474 | 0.9232 | 84.0% | 72,999 |
| **+ model on invoice→bank** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 85.7% | 42 |

Accuracy is also insensitive to how *dense* the defects are. Scaling every
defect rate up (`benchmark.py --scale N`, 12 seeds each) does not degrade
matching — what falls is how much can be cleared, which is correct, because
more of the corpus is genuinely broken:

| defect scale | defects / records | link F1 | exc P | exc R | auto-clear |
|---:|---|---:|---:|---:|---:|
| 1× | 61 / 340 | 0.8767 | 0.8972 | 0.9456 | 83.7% |
| 2× | 122 / 340 | 0.8798 | 0.8935 | 0.9435 | 71.4% |
| 3× | 179 / 337 | 0.8833 | 0.8926 | 0.9429 | 59.5% |
| 4× | 215 / 332 | 0.9639 | 0.9640 | 0.9811 | 53.2% |
| 6× | 241 / 327 | 1.0000 | 1.0000 | 1.0000 | 39.5% |

Accuracy *rising* past 3× is not the engine getting better — it is the
generator saturating. Beyond about 3× the order pool is exhausted, so the
hardest defect type (a direct bank payment with a mangled counterparty name)
stops being generated at all, leaving only cases the deterministic tiers handle
exactly. **Defect density is not the axis that stresses this engine.** I did not
find a density at which it produces a wrong match; see the limitations.

Every seed is reproducible: `kosh generate --seed 13`.

### Where the money is

The report leads with a cash bridge, not a match rate, because that is the
question a controller is actually asking:

```
  Captured at the gateway                  7,26,213.94
  Less funds on hold                        -19,919.85
  Less captures not yet batched                  +0.00
= Gross entering settlement                7,06,294.09
  Less refunds settled                      -40,711.41
  Less gateway fees                          -8,967.77
  Less GST on fees                           -1,614.19
  Less dispute adjustments                   -3,751.34
= Net settled into batches                 6,51,249.38
  Batches say                              6,51,249.38
  Residual (must be zero)                        +0.00
  Landed in the bank                       5,79,299.00
  Still in transit                            71,950.38
```

The residual stays on the face of the report rather than being plugged into a
subtotal. It earned its place immediately — see
[docs/architecture.md §6](docs/architecture.md).

### The exception list

Fourteen closed exception codes, no open-ended "other" bucket. Each finding
carries its evidence, its rupee exposure, and what a controller should do:

```
UNEXPECTED_BANK_CREDIT        10   needs review
UNPAID_INVOICE                 9   needs review
UNBILLED_PAYMENT               5   needs review
FEE_VARIANCE                   5   auto-resolved
DUPLICATE_PAYMENT              4   needs review
FUNDS_ON_HOLD                  4   needs review
TAX_LINE_MISMATCH              4   needs review
MERGED_PAYOUT                  4   auto-resolved
ORPHAN_REFUND                  3   needs review
CHARGEBACK_ADJUSTMENT          3   needs review
SETTLEMENT_AMOUNT_MISMATCH     3   needs review
MISSING_IN_BANK                3   needs review
SPLIT_SETTLEMENT               2   auto-resolved
```

A worked example, straight out of `outputs/recon.md`:

> **`setl_82400005`** · `MISSING_IN_BANK` · **₹44,994.34**
> utr=`KKBKN260708563337`; settled_at=`2026-07-08T11:00:00`; members=`7`
> *Trace UTR KKBKN260708563337 with the bank. Until it lands, 44994.34 sits in
> gateway receivable, not in cash.*

---

## Where the model is — and where it deliberately is not

**The model never decides whether two records match.** Matching is arithmetic
and identifier logic. That is the part an auditor examines, and "the model said
so" is not an audit trail.

It is asked exactly one question, on one leg: *a customer paid the bank
directly, the amount is right, but the narration has mangled their name — which
open invoice is this?* Reading `MERIDIAN LBS` as Meridian Labs is genuinely
beyond a token-overlap threshold and genuinely within a 1.5B model's reach.

Three guards hold it in place:

1. **Fixed candidate set** — it returns one of the options offered, or NONE. It
   cannot name a record that was not on the list.
2. **Arithmetic disposes** — the chosen invoice's gross must equal the credit,
   or equal it net of a statutory TDS rate (1%, 2%, 10% — sections 194C/194J).
   Failures are logged `rejected_by_arithmetic` and discarded.
3. **A literal name token is required** — the candidate must share at least one
   word with the narration.

It was wired into all four legs first, then measured (`scripts/ablation.py`):

| configuration | link F1 | exception F1 | LLM calls | LLM seconds |
|---|---:|---:|---:|---:|
| deterministic only | 0.8889 | 0.9217 | 0 | 0.0 |
| model on every leg | 1.0000 | 1.0000 | 18 | 46.4 |
| **model on invoice→bank only** | **1.0000** | **1.0000** | **3** | **9.3** |

Same accuracy from three calls instead of eighteen. On the other legs the
residual is records with no counterparty *in the data at all* — an invoice
nobody paid, a batch the bank has not sent — so there is nothing to find and
every answer is a false positive. Of its 15 calls there, 13 were declines and 2
were wrong picks the arithmetic gate caught.

---

## Honesty machinery

This is the part the track is really testing, so it is worth being explicit.

- **Ground truth is held out behaviourally, not by convention.** The generator
  writes `ground_truth.json`; only `kosh.evaluate` opens it. A test deletes the
  file and asserts the run produces byte-identical matches and findings.
- **Classification is scored strictly**, over `(record, code)` pairs — the right
  label on the wrong row costs a false positive *and* a false negative.
- **`auto_clear_rate` is conservative**: a record counts as cleared only if it
  was correctly linked *and* carries nothing needing a human.
- **Unparseable rows are surfaced, never dropped.** A reconciliation that
  quietly skips six rows shows a lovely match rate on the rows it kept.
- **Thirty seeds, not one.** A single corpus written by the same person as the
  matcher on the same afternoon proves nothing.
- **The failures are written down** — including one where the model linked a
  bank interest credit to a customer invoice. See
  [docs/architecture.md §7](docs/architecture.md).

---

## Settlement Q&A — and where the model is not good enough

```bash
./.venv/bin/kosh ask "why has setl_82400005 not reached the bank?"
```

Answers are assembled from the run's own matches, findings and cash position. If
nothing in the run bears on the question, it says so.

**This defaults to `--llm off`, on evidence.** The same 1.5B model that was
correct on all 90 adjudications across the benchmark is unreliable at prose: on
this run it invented a total by summing four on-hold balances that are excluded
from the figure it was explaining, wrote a dollar sign on a rupee amount, and
asserted a batch had been "partially credited" when the finding was that no
credit arrived at all. Grounded numbers, invented reasoning.

So with `--llm on`, three guards apply — every amount in the answer must already
appear in the retrieved facts, aggregation vocabulary (`total`, `combined`,
`sum of`) is rejected outright, and a rejected answer is replaced by the raw
records rather than reworded:

```
A: The model produced amounts that are not in the underlying records (15467.80),
   so its wording is withheld. The relevant records are:
     [FUNDS_ON_HOLD] pay_82400115 — exposure 8,033.51. captured_at=2026-07-10…
```

The lesson generalises to the rest of the design: **this model is good at
choosing among candidates and bad at generating explanations**, so it is used
for the former and boxed out of the latter.

---

## Repository map

```
src/kosh/
  money.py      integer-paise arithmetic — nothing here is ever a float
  schema.py     the three sources, and the closed exception taxonomy
  generate.py   synthetic corpus + ground truth, seeded and reproducible
  ingest.py     tolerant parsing; failures surfaced with line numbers
  normalize.py  UTR extraction and name normalisation from messy narrations
  match.py      the five-tier cascade — the engine
  position.py   the cash bridge, with an identity that must hold
  llm.py        the local model layer, boxed in
  ask.py        grounded settlement Q&A
  evaluate.py   the only module that opens ground truth
  report.py     Markdown / HTML / JSON renderings of one payload
  cli.py        `kosh generate | recon | evaluate | ask`
scripts/
  benchmark.py       accuracy across N regenerated worlds
  ablation.py        does the model earn its place, and where
  verify_offline.py  the full pipeline with all sockets blocked
docs/architecture.md   design, measurement, and everything that went wrong
```

---

## Requirements

Python 3.12 (PyTorch has no 3.14 wheels yet). The core engine needs only
`numpy`, `scipy` and `jsonschema` — about 40 MB. The optional model layer adds
`torch` + `transformers` and a one-time ~3 GB weight download, after which it
runs with `HF_HUB_OFFLINE=1`.

Developed on an Apple M2, 16 GB, macOS 26.1, PyTorch 2.8 on Metal (MPS). The
model loads in 6.4 s and each adjudication takes ~3 s. It runs on CPU roughly
4–6× slower; the deterministic engine is unaffected either way.

Model pinned in [`models.lock.json`](models.lock.json):
`Qwen/Qwen2.5-1.5B-Instruct` @ `989aa79`, Apache-2.0.
