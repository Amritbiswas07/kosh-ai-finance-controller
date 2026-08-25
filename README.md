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
| `kosh serve` | Browse and work the run in a local web UI |
| `kosh sync` | Reconcile and fold the result into the running ledger |
| `kosh pull --year --month [--day]` | Fetch a real settlement recon period from Razorpay |
| `kosh exception list \| assign \| resolve \| link` | Work a break: own it, close it, or confirm a link |
| `pytest -q` | 205 tests, no model loaded, ~2 s |
| `python scripts/benchmark.py --seeds 30 [--llm]` | Accuracy across 30 regenerated worlds |
| `python scripts/ablation.py` | Does the model earn its place, and on which leg? |
| `python scripts/adversarial.py [--llm]` | Score it on data written **against** it |
| `python scripts/live_demo.py` | Three days of a close: a late credit clears yesterday's break |
| `python scripts/verify_offline.py` | Run everything with all outbound sockets blocked |
| `python scripts/check_docs.py` | Assert every figure in these documents is still true |
| `python scripts/baseline.py [--llm]` | What this is worth against a spreadsheet |

```bash
./.venv/bin/kosh serve
```

Opens a working UI at `http://127.0.0.1:8000` — see below.

---

## The web interface

```bash
./.venv/bin/kosh serve --port 8000
```

Not a prettier report: the controller's actual workflow. Pick a seed, run it
live, then work the queue.

- **Run it from the header** — change the seed, toggle model adjudication, press
  Reconcile. A model-enabled run takes ~10 s, so the pipeline **streams stage by
  stage** over Server-Sent Events rather than showing an indefinite spinner.
- **The top bar is pinned and lifts off the content** — it takes a shadow once
  the page has actually moved and stays flat at rest, so rows pass visibly
  beneath it rather than appearing to bleed into it. Once the page heading has
  scrolled away the bar names the current section in its place.
- **Navigation is a pinned sidebar above 1000px**, and the same panel collapses
  to a menu-button drawer below it — dismissable by the close button, the scrim
  or Escape, returning focus where it came from. Docking is expressed in JS
  rather than by overriding the `hidden` attribute in CSS, because a visible
  element marked `hidden` lies to a screen reader. Resizing across the
  breakpoint re-docks without a reload. The current section is the page's
  `<h1>` and its document title.
- **Overview** — the cash bridge with its residual, the tier breakdown, and the
  scores against held-out ground truth.
- **Exceptions** — every finding, filterable by code and by whether it needs a
  person, searchable across ids, narrations and customers, sorted by exposure.
  Expand any row for its evidence and proposed action.
- **Model** — the adjudication log: candidates offered, what was chosen, and
  whether the arithmetic accepted or rejected it.
- **Ask** — settlement Q&A against the run that is loaded.

**Styled to Razorpay.** Sizes come off one scale rather than being picked per
component — type 11/12/13/14/15/17/20/26, space 4/8/12/16/24/32/48, radius
8/12/40 — and the six summary tiles wrap 6 → 3 → 2 so no row is ever left
ragged. The palette is sampled from the live razorpay.com — 
`#305EFF` primary, `#132644` navy ink, `#F0F4F6` surface, `#ED2939` red, and
their signature fully-rounded 40px pills — rather than recalled from memory. The
static pack in `outputs/recon.html` uses the same tokens, so the report and the
app read as one product. Both themes are token-defined for all three viewer
states, and dark mode clears WCAG AA comfortably (ink 15.8:1, muted 7.15:1).

**The brand mark.** The official Razorpay SVG sits at
`src/kosh/static/razorpay-logo.svg` and is **inlined** into both the app header
and the report masthead — not referenced with `<img src>`. That matters twice
over: only an SVG inside the document can inherit `currentColor`, which is what
lets the mark's dark half invert on the navy dark theme instead of disappearing
into it; and the pack is published under a CSP that blocks external hosts, so a
referenced logo would simply not render.

Two adjustments to the asset, both recorded because they are the kind that look
like nothing: as supplied the artwork occupies a thin band of a 960-square
canvas — measured at `x 26.8, y 384.4, 905.5 × 191.3` — so at header size it
rendered about five pixels tall. The stored copy tightens the `viewBox` to those
bounds; the geometry is untouched, only the window onto it. And the navy fill
`#192839` became `currentColor` while Razorpay's blue `#3395ff` is left exactly
as supplied. Remove the file and both surfaces fall back to a plain tile.

**Standard library only.** [`server.py`](src/kosh/server.py) is a
`ThreadingHTTPServer`, not FastAPI — the web UI adds **zero dependencies**. It
binds to localhost and serves one self-contained page with no CDN links and no
webfonts, so it renders with the machine unplugged. (Mona Sans, Razorpay's own
face, is not on a permitted font host, so the type is a system stack tuned to
sit close to it.) A test asserts the page *fetches* nothing external — every
`src`, `url()` and stylesheet link stays local, while a plain hyperlink to the
buildathon page is allowed, since clicking it is the reader's choice.

Three bugs worth recording, since two of them only appear under use:

- **`Connection: close` on the SSE response is load-bearing.** With no
  `Content-Length` and no chunked encoding, closing the socket is the browser's
  only end-of-stream signal; on keep-alive the reader never reports done and the
  page stays stuck showing the first run as still going.
- **The model was loaded outside the lock.** An `/api/ask` arriving while a run
  was in flight put two threads into `from_pretrained(...).to(device)` at once,
  and torch failed the second with `Cannot copy out of meta tensor` — a
  confusing error a long way from its cause. Loading now has its own lock.
- **`"bank" in narration` matched `method=netbanking`.** Q&A retrieval used
  substring tests, so every netbanking payment was a hit for "bank" and a ₹2.72
  fee variance outranked a ₹44,994 settlement that had genuinely not arrived.
  Retrieval now compares word tokens, and equal relevance is broken by exposure.

---

## What Track 4 asked for, and where it is

| The ask | Where |
|---|---|
| One financial-operations workflow | Three-way settlement reconciliation, four legs |
| 50+ synthetic data records | **347** per run (135 invoices, 151 gateway rows, 61 bank lines) — 10,388 across the benchmark |
| Report match accuracy | Precision/recall/F1 per leg, scored against held-out ground truth |
| Report unresolved exceptions | Every one, itemised, with evidence, exposure and a proposed action |
| Throughput plus measured accuracy | See below — both, measured, not estimated |
| Multiple matches, not cherry-picked | 30 regenerated corpora with varying defect rates; distribution reported |

---

## Results

**347 records, reconciled in five milliseconds.** The deterministic engine
itself takes **1.0 ms**; end to end including CSV parsing it is **5.0 ms**, or
about **69,700 records/second**. With the model enabled, wall time is dominated
entirely by the three adjudication calls — the arithmetic is unchanged.

### What it is worth against a spreadsheet

F1 is not money. The way these books get reconciled today is an exact-identifier
lookup and then eyeballing the rest, so `scripts/baseline.py` reconciles the same
corpus that way and reports the difference in rupees:

| how the books get reconciled | links | recall | left to do by hand |
|---|---:|---:|---:|
| exact identifier only (a spreadsheet) | 145/173 | 83.8% | 3,62,069.84 |
| deterministic tiers | 170/173 | 98.3% | 17,933.07 |
| **with the model** | **173/173** | **100.0%** | **0.0** |

**3,62,069.84 comes off the desk** of whoever
would otherwise work through it by hand, on a corpus of 347 records.

Across **30 regenerated corpora (10,383 records)**, each with independently
jittered defect rates:

| configuration | link F1 (mean) | link F1 (min) | exc P | exc R | exc F1 | auto-clear | records/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| deterministic only | 0.8898 | 0.7778 | 0.9017 | 0.9561 | 0.9280 | 82.5% | 68,342 |
| **+ model on the legs it can help** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 84.5% | 16 |

Every seed is reproducible: `kosh generate --seed 13`.

### Where the money is

The report leads with a cash bridge, not a match rate, because that is the
question a controller is actually asking:

```
  Captured at the gateway                 +   7,24,300.88
  Less funds on hold                      -     19,919.85
  Less captures not yet batched           -      9,822.75
  = Gross entering settlement                6,94,558.28
  Less refunds settled                    -     26,511.62
  Less gateway fees                       -      8,923.02
  Less GST on fees                        -      1,606.15
  Less dispute adjustments                -      7,474.61
  = Net settled into batches                 6,50,042.88
  Batches say                                6,50,042.88
  Residual (must be zero)                 +          0.00
  Landed in the bank                      +   6,25,607.82
  Still in transit                        +     24,435.06
```

The residual stays on the face of the report rather than being plugged into a
subtotal. It earned its place immediately — see
[docs/architecture.md §6](docs/architecture.md).

### The exception list

Fourteen closed exception codes, no open-ended "other" bucket. Each finding
carries its evidence, its rupee exposure, and what a controller should do.
This is the full run with adjudication on — 56 findings, 42 needing a human:

```
UNEXPECTED_BANK_CREDIT        7   needs review
UNPAID_INVOICE                6   needs review
AWAITING_SETTLEMENT           5   needs review
FEE_VARIANCE                  5   auto-resolved
UNBILLED_PAYMENT              5   needs review
DUPLICATE_PAYMENT             4   needs review
FUNDS_ON_HOLD                 4   needs review
MERGED_PAYOUT                 4   auto-resolved
TAX_LINE_MISMATCH             4   needs review
CHARGEBACK_ADJUSTMENT         3   needs review
MISSING_IN_BANK               3   needs review
ORPHAN_REFUND                 3   needs review
PART_PAYMENT                  3   auto-resolved
SETTLEMENT_AMOUNT_MISMATCH    3   needs review
SHORT_PAYMENT                 3   needs review
TDS_WITHHELD                  3   auto-resolved
OVERPAYMENT                   2   needs review
SPLIT_SETTLEMENT              2   auto-resolved
```

A worked example, straight out of `outputs/recon.md`:

> **`setl_82400025`** · `MISSING_IN_BANK` · **₹20,402.61**
> utr=`SBIN0260728871951`; settled_at=`2026-07-28T11:00:00`; members=`4`;
> gross=`21,042.99`; fee_and_gst=`640.38`
> *Trace UTR SBIN0260728871951 with the bank. Until it lands, 20402.61 sits in
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
| deterministic only | 0.8889 | 0.9280 | 0 | 0.0 |
| model on every leg | 1.0000 | 1.0000 | 18 | 47.5 |
| **model on the two legs it can help** | **1.0000** | **1.0000** | **6** | **21.2** |

Three of those six calls are spent on the settlement leg and every one is
rejected by the arithmetic gate — on *this* corpus there is nothing there to
find, exactly as the first ablation said. They are kept because the same call
is what recovers 4 of 4 links against statement formats the extractor cannot
parse (§8). The cost of carrying it is three wasted calls a run; the cost of
dropping it is every unfamiliar narration going unmatched.

Same accuracy from six calls instead of eighteen. On the other legs the
residual is records with no counterparty *in the data at all* — an invoice
nobody paid, a batch the bank has not sent — so there is nothing to find and
every answer is a false positive. Of its 15 calls there, 13 were declines and 2
were wrong picks the arithmetic gate caught.

---

## It is a control, not a report

Exceptions have an owner and a lifecycle, closing a large one needs a second
name that cannot be the first, and every action is attributable:

```bash
./.venv/bin/kosh exception assign --key setl_82400025 --code MISSING_IN_BANK \
    --to priya --by amrit
./.venv/bin/kosh exception write-off --key pay_82400052 --code UNBILLED_PAYMENT \
    --by priya --note "unrecoverable after 90 days" --approved-by amrit
```

**And it learns from the person.** When a controller links two records the
engine could not, every later run replays that decision at a tier above all
others — asking again each morning is not diligence, it is the tool forgetting:

```
$ kosh exception link --key setl_82400020 --to bank:0004 --by amrit
$ kosh sync
  replaying 1 link(s) a person already confirmed
    - bank:0004      UNEXPECTED_BANK_CREDIT   8,936.54   matched once the data arrived
    - setl_82400020  MISSING_IN_BANK          1,645.52   matched once the data arrived
```

The confirmations are *passed into* `reconcile()` rather than read by it, so the
engine keeps no hidden state. The web UI has a **Ledger** view showing the same
picture — what is open, who owns it, how old it is, and the audit trail.

---

## It reads what banks actually send

`kosh.feeds` reads **MT940**, the SWIFT statement format Indian banks export
from corporate net-banking — comma decimals, two-digit years, dates split across
two fields, narrations continued across lines, and a debit/credit marker that is
a letter inside a field rather than a column. Drop a `bank_statement.sta` beside
the other files and it is used instead of the CSV. The parser also checks the
statement against its own opening and closing balances, because one that does
not add up has been truncated.

---

## It reads the real API, not just a CSV that looks like one

```bash
export RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
export RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxx
./.venv/bin/kosh pull --year 2026 --month 7
```

`GET /v1/settlements/recon/combined` is the report the synthetic CSV was written
to imitate. `kosh pull` fetches it, maps it onto the engine's own records and
writes it in the same shape the generator emits — so a pulled period and a
generated one are read by **the same parser** and reconciled by the same engine.

**Read-only by construction.** One request method, it issues `GET`, and there is
a test asserting the strings `POST`, `PUT`, `PATCH` and `DELETE` appear nowhere
in the module. Nothing in Kosh can move money.

**Credentials are read from the environment and never stored, logged or
printed** — they go into the `Authorization` header and nowhere else, and are
scrubbed from every error message, since an API error that echoes its request
URL is the ordinary way a key reaches a log file.

Three things about the live API differ from the CSV, and each silently produces
wrong money if missed:

| | CSV | API |
|---|---|---|
| Amounts | rupee strings, parsed with `to_paise` | **already integer paise** — reparsing multiplies by 100 |
| Timestamps | ISO strings | Unix epochs |
| Direction | sign of `amount` | the `debit` / `credit` columns; `amount` is unsigned |

That middle column is why the engine has used integer paise from the first
commit: Razorpay's own API speaks paise, so the representation matches the
source rather than being converted at the boundary.

**Verified against the documented shape, not a live account.** I have no
merchant credentials, so `tests/fixtures/razorpay_recon.json` carries the
response structure from Razorpay's docs with invented values, and twelve tests
pin every unit, null and edge — including a row that cannot be mapped, which is
reported rather than dropped. The network call itself is untested against a real
key; that is the one part of this path that a live account would exercise.

---

## It keeps state, so a late credit clears yesterday's break

A settlement sent on Monday reaches the bank on Wednesday. An exception raised
today is routinely answered by data that does not exist yet, so a tool that
starts from nothing every morning cannot tell you a break has cleared.

```bash
./.venv/bin/python scripts/live_demo.py
```

```
Day 1  343 records, 343 new      opened  setl_82400041  MISSING_IN_BANK  23,733.74
Day 2  347 records,   4 new      CLEARED setl_82400041  MISSING_IN_BANK  23,733.74
                                         (matched once the data arrived)
Day 3  347 records,   0 new      nothing changed
```

The engine did not change between day 1 and day 2 — the data caught up. Day 3
loads the same export again and nothing moves, because every row carries a
content fingerprint. The matching engine still knows nothing about the store; a
test asserts it never reads it.

---

## Scored on data written against it

`generate.py` cannot escape being written by the same author as the matcher.
`kosh.adversary` is built to be unfair — cases chosen by asking what actually
breaks a close, most with no code in the taxonomy:

| | deterministic | + model |
|---|---:|---:|
| links recovered | **1 / 4** | **4 / 4** |
| false links created | 1 / 8 | 1 / 8 |
| **invented a cause it could not know** | **0 %** | **0 %** |

Three payouts of the same amount on the same day, references in a statement
format no pattern reads: amount and date are useless, so the reference is the
only signal. **This is where the model stops being a garnish** — rules get one
of four, reading the narration gets four of four.

---

## Properties, not just cases

Every other test here checks a situation I imagined — the exact critique worth
taking seriously. `tests/test_invariants.py` asserts properties that must hold
for **any** input, over sixty randomly built and randomly damaged corpora:
no record disappears silently, a match that hides a difference must say so, the
bridge always balances, nothing is claimed twice, and the answer never depends
on row order.

It found three real defects on its first run — a blank identifier matching
another blank identifier, a re-sent export row loaded twice, and an unsettled
refund that nothing in the report mentioned. The disclosure invariant is the
general form of the ₹9,900 hole that shipped for weeks.

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
  cli.py        `kosh generate | recon | evaluate | ask | serve`
  server.py     stdlib web UI — streams the pipeline, serves the queue
  static/       the single self-contained page it serves
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
