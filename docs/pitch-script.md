# Five-minute pitch — shot list and script

Track 4 asks for a public repo, a 5-minute video and an architecture writeup.
This is the video.

**Before you record**

```bash
./.venv/bin/kosh generate --seed 20260824     # canonical corpus
./.venv/bin/kosh serve --port 8000            # leave running in one terminal
```

Have ready: a browser at `http://127.0.0.1:8000`, a second terminal in the repo,
`docs/architecture.md` open, and `outputs/ablation.md`. Dark or light — pick one
and stay in it. Numbers below are the current run; re-read them off your own
screen if you regenerate.

---

## 0:00 – 0:30 · The problem

**SHOW** `data/bank_statement.csv` in the terminal — scroll the narration column.

**SAY**

> A merchant on a payment gateway has three systems that should agree and never
> do. The ERP says what was invoiced. The gateway says what was captured and what
> it paid out. The bank says what actually arrived.
>
> Between a card being charged and the money being spendable there's a fee, GST
> on that fee, a refund window, a settlement batch, a dispute reserve, and a bank
> transfer. At every step money can go missing or arrive changed — and at
> month-end a controller has to explain every rupee of the difference to an
> auditor.
>
> Look at what they have to work with. The reference that ties a settlement to a
> bank credit is a sixteen-character UTR, and by the time it reaches a statement
> export it's been lower-cased, wrapped in slashes, or glued to a company name.
> Sometimes it isn't there at all.

---

## 0:30 – 1:20 · Run it

**SHOW** The browser. Press **Reconcile** with *Model adjudication* on. Let the
stage list stream.

**SAY**

> This is Kosh. Three hundred and forty-five records across those three sources.
>
> The engine takes under a millisecond — about sixty-nine thousand records a
> second. What you're watching is the model, and it's called exactly three times.
>
> Cash position first, not a match rate, because that's the question actually
> being asked. Six and a half lakh settled into batches. Five point seven nine
> landed in the bank. Seventy-two thousand still in transit. And a residual line
> that has to be zero — I'll come back to that one.

---

## 1:20 – 2:20 · How it matches, and where the model is

**SHOW** The **What reconciled** tier table. Then `src/kosh/match.py`, scrolling
the tier constants.

**SAY**

> Matching is five tiers, each one running only on what the tier above couldn't
> resolve. Exact identifier. Normalised identifier. Then, where there's no shared
> ID at all, a globally optimal assignment — the Hungarian algorithm, not greedy
> nearest-neighbour, because greedy lets an early row claim a partner a later row
> needed, and your reconciliation would depend on how the CSV happened to be
> sorted. Then aggregate matching for split credits and consolidated payouts.
>
> And here's the design decision I'd defend hardest. **The model never decides
> whether two records match.** Matching is arithmetic, and it's the part an
> auditor examines. "The model said so" is not an audit trail.
>
> The model gets one question, on one leg. A customer paid the bank directly, net
> of two percent TDS, and the narration has mangled their name to `MERIDIAN LBS`.
> No threshold recovers that. A 1.5B model reads it as Meridian Labs — and then
> the arithmetic re-checks the pick before it's accepted.

---

## 2:20 – 3:00 · The evidence

**SHOW** `outputs/ablation.md`.

**SAY**

> I didn't decide that by taste. I wired adjudication into all four legs first,
> then measured.
>
> Same accuracy from three model calls as from eighteen. On the other legs the
> leftovers are records with no counterparty *in the data at all* — an invoice
> nobody paid, a batch the bank hasn't sent. There's nothing to find, so every
> answer is a false positive. Of fifteen calls there, thirteen were declines and
> two were wrong picks that the arithmetic gate caught and threw away.

---

## 3:00 – 3:45 · The honest half

**SHOW** The **Exceptions** section. Filter to *Needs review*. Expand one
`MISSING_IN_BANK` row.

**SAY**

> The track asks for match rate **and** the exceptions it couldn't resolve. This
> is the second half, and I think it's the harder one.
>
> Fourteen closed exception codes — no open-ended "other" bucket, because that's
> where an engine hides its failures. Forty-two items need a person, carrying two
> point four lakh of exposure. Every one carries its evidence and what to do
> about it.
>
> This batch of forty-five thousand rupees left the gateway on the eighth and no
> bank credit has landed. Until it does, that's gateway receivable, not cash.
> That's a sentence a controller can act on.

---

## 3:45 – 4:35 · Measurement

**SHOW** `outputs/benchmark_llm.json`, then `docs/architecture.md` §7.

**SAY**

> A single corpus, written by the same person as the matcher, on the same
> afternoon, proves nothing. So it regenerates the whole world thirty times with
> independently varied defect rates. Ten thousand records. Link F1 and exception
> F1 both hold at one point oh across all thirty; deterministic-only sits at
> 0.89 and 0.92, which is the model's actual contribution.
>
> And the failures are written down. The evaluator caught a bug in my own data
> generator. My stress sweep reported an accuracy collapse that turned out to be
> my own metric averaging in empty legs. And the model once linked a bank
> interest credit to a customer invoice, because the amount coincidentally
> matched a TDS deduction — the fix was a deterministic guard, not a better
> prompt.

---

## 4:35 – 5:00 · Close

**SHOW** `scripts/verify_offline.py` running to PASS.

**SAY**

> All of it runs on this laptop. Open weights, loaded from a local cache, with
> every outbound socket blocked — no API key, no per-token cost, and a
> reconciliation you can reproduce from a seed.
>
> A hundred and twenty-five tests. The engine's numbers are measured against
> ground truth it is structurally unable to read. And where it fails, it says so.

---

## Notes

- ~720 words. At a natural 150 wpm that lands near 4:50, leaving room to breathe.
- The one thing to *not* rush is 1:20–2:20. The "model never decides a match"
  argument is the differentiator; everything else is table stakes.
- If you overrun, cut 3:45–4:35 down to the thirty-seed number alone.
- Don't read the metrics off this page. Read them off your screen, live.
