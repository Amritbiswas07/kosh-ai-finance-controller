# Eight-minute pitch — shot list and script

Plain language throughout: a judge should follow it whether or not they have
ever closed a set of books.

> **One thing to decide before you record.** The buildathon asks for a
> **five-minute** video. This script runs about eight. If the limit is enforced,
> cut the three sections marked **[CUT FIRST]** — that lands it near five
> minutes and loses nothing a judge needs. Everything else is in the order that
> makes the argument.

**Before you record**

```bash
./.venv/bin/kosh generate --seed 20260824     # build the data
./.venv/bin/kosh serve --port 8000            # leave this running
```

Have ready: a browser at `http://127.0.0.1:8000`, a second terminal in the repo,
`docs/architecture.md` open, and `outputs/` for the evidence files. Pick dark or
light and stay in it. Read the numbers off your screen, not off this page.

---

## 0:00 – 0:40 · The problem

**SHOW** `data/bank_statement.csv` in the terminal. Scroll slowly through the
description column so people see the mess.

**SAY**

> If you sell online, three systems track your money and none of them agree.
> Your accounting software says what you invoiced. Your payment provider says
> what it collected and paid out. Your bank says what actually arrived.
>
> They differ because the provider takes a fee, adds tax on it, holds money back
> for refunds, and pays in batches days later. At month end somebody has to
> explain every rupee of that gap, and prove it to an auditor.
>
> And this is what they get to work with. Every payout carries a reference — and
> by the time the bank prints it, it looks like this. Different case, stuck to a
> company name, sometimes just missing.

---

## 0:40 – 1:30 · Watch it run

**SHOW** The browser. Tick **Model adjudication**, press **Reconcile**, let the
progress list run.

**SAY**

> This is Kosh. Three hundred and forty-seven records from those three sources.
>
> The matching finishes in about a millisecond — sixty-eight thousand records a
> second. What you are waiting on is the AI, and it runs a handful of times.
>
> The first thing you see is not a score. It is where the money is. Six and a
> half lakh went out in batches; six point two five lakh reached the bank.
>
> And this line has to be zero. If the arithmetic does not hold it says so, rather
> than quietly burying the difference. It caught a real bug in my own code the
> first week — I was deducting fees from money that had never left.

---

## 1:30 – 2:30 · How it matches, and what the AI is for

**SHOW** The **What reconciled** table, then `src/kosh/match.py`, scrolling past
the tier names.

**SAY**

> Matching happens in five passes, each looking only at what the pass before it
> could not solve.
>
> The easy ones first — both sides carry the same reference. Then the same
> reference written differently. Then the hard ones, with nothing shared, matched
> on amount and date. For those it finds the best set of pairings across the whole
> file at once, rather than grabbing the nearest match row by row. That matters:
> grabbing as you go lets an early row take the partner a later row needed, so
> your answer changes if the file was sorted differently.
>
> And when two candidates are equally good, it refuses. A coin toss is not a match.
>
> Now the decision I would defend hardest. **The AI never decides whether two
> records match.** That is arithmetic — it is the part an auditor checks, and "the
> AI thought so" is not an answer you can give them.

---

## 2:30 – 3:20 · Where the AI does earn its place

**SHOW** An `unseen_narration` case from `outputs/adversarial.json`, or the
Model tab in the browser.

**SAY**

> Here is what it is for. A customer paid our bank directly, held back tax, and
> the bank mangled their name to `MERIDIAN LBS`. No rule catches that. A person
> reads it instantly as Meridian Labs — and so does a small AI model.
>
> Then arithmetic checks the answer. The model picks from a list I built, cannot
> name anything off that list, and if the amount does not work out the pick is
> thrown away however confident it sounded.
>
> On statement formats no pattern can read, rules alone recover one link in four.
> Reading the narration recovers four in four.

---

## 3:20 – 4:00 · Proving that was the right call **[CUT FIRST]**

**SHOW** `outputs/ablation.md`.

**SAY**

> I did not just decide that felt right. I let the AI loose on every part of the
> job first, then measured both ways. Identical accuracy from six calls as from
> eighteen.
>
> Everywhere else the leftovers have no match anywhere in the data — an invoice
> nobody paid, a payout the bank has not sent. Nothing to find, so anything it
> says is wrong. Of fifteen tries there it passed on thirteen, and both guesses
> were caught by the arithmetic.

---

## 4:00 – 5:00 · The AI writes rules, not answers

**SHOW** `kosh rule add "..."` in the terminal, then the printed rule and its
backtest.

**SAY**

> Everything so far has the AI making one-off decisions. Useful — and the least
> durable thing a model can do, because the judgement disappears when the run ends
> and tomorrow it gets asked again.
>
> So it has a second job, and this is the one I think matters. A controller types
> what they know, in English.
>
> The model decides nothing. It reads that sentence and returns fields and
> thresholds, from a fixed list it is allowed to use and nothing else. Here is
> what came back, in plain English, for the person who asked.
>
> Then it is tested against books whose answers we already know. Three links, all
> correct. Only now can it be switched on — and from there it runs as ordinary
> arithmetic, forever, with the author's name on every match.
>
> The model reads intent, which no pattern can. The rule does the matching, which
> no model should. And those three links used to be the AI's job every single
> run. Stated once, they are arithmetic, and it is not consulted at all.
>
> It reads the instruction right seven times in eight. The one it gets wrong made
> a rule that matched nothing, and the backtest said so. A wrong reading costs a
> retype, not money.

---

## 5:00 – 5:50 · The part nobody likes showing

**SHOW** The **Exceptions** section. Filter to *Needs review*. Open one
`MISSING_IN_BANK` row.

**SAY**

> The brief asks for the match rate **and** what it could not sort out. This is
> that second half, and it is the harder one.
>
> Eighteen possible reasons something did not match, and the list is fixed. There
> is deliberately no "other" bucket, because that is where a tool like this hides
> its failures.
>
> Fifty-two items need a human, worth about two point three lakh. Each shows its
> evidence and what to do. This one: money left the provider and no matching
> credit reached the bank. That is not cash — it is money you are owed.
>
> And when it does not know, it says so rather than picking the nearest label.
> That exists because early on it called a currency conversion a
> four-thousand-rupee bank charge. Plausible sentence. Wrong answer.

---

## 5:50 – 6:30 · It is a control, not a report

**SHOW** The **Ledger** tab, then `scripts/live_demo.py` output.

**SAY**

> A payout sent on Monday reaches the bank on Wednesday, so a problem raised today
> is often answered by data that does not exist yet. A tool that starts from
> nothing every morning cannot tell you a break has cleared.
>
> So it remembers. Day one a payout is missing. Day two the statement catches up
> and the break clears itself — the engine did not change, the data did. Day three
> the same file is loaded again and nothing moves, because every row is
> fingerprinted.
>
> Exceptions get an owner. Closing a large one needs a second name, and it cannot
> be the same name.

---

## 6:30 – 7:20 · How I know the numbers are real

**SHOW** `outputs/benchmark.json`, then `tests/test_invariants.py`, then
`docs/architecture.md` section 7.

**SAY**

> One set of test data, written by the same person who wrote the matcher, proves
> nothing. So it rebuilds the whole dataset from scratch thirty times — ten
> thousand records, each with a different mix of problems.
>
> But that still only tests what I thought of. So a second suite asserts things
> that must be true of **any** input, over sixty randomly damaged datasets. No
> record disappears silently. A match that hides a difference has to say so. The
> books always balance. The answer never depends on row order.
>
> That found three real bugs the day I wrote it — including one where a record
> with no reference matched another record with no reference. Two things sharing
> nothing but an absence, linked.
>
> And I wrote down what went wrong. My own metric once reported a collapse that
> never happened. The AI once matched a bank interest payment to a customer
> invoice because the amount lined up — fixed with a hard rule, not a prompt.

---

## 7:20 – 8:00 · What it is worth, and close **[CUT FIRST for time]**

**SHOW** `scripts/baseline.py` output, then `scripts/verify_offline.py` running
through to PASS.

**SAY**

> Last thing, because accuracy is not the point — money is. These books get done
> today with a spreadsheet lookup on the reference, then eyeballing the rest. So I
> did the same data that way and compared.
>
> The spreadsheet gets eighty-four percent and leaves three point six lakh to work
> through by hand. This gets all of it. That difference is the product.
>
> And it all runs on this laptop. The model is free and open, loaded from disk —
> here it is with the internet switched off entirely. No API key, no cost per use.
>
> Two hundred and twenty-six tests. Every accuracy number is checked against an
> answer key the program is built so it cannot read. And when it cannot work
> something out, it says so.

---

## Notes

- 1,300 spoken words: about **7:53** at a presenting pace of 165 words a
  minute, or 8:40 if you speak deliberately at 150. Time yourself once on
  section 1:30–2:30 and you will know which you are.
- **If you must hit five minutes**, cut the two **[CUT FIRST]** sections and
  trim 6:30–7:20 to the thirty-datasets line alone. That lands near 5:10.
- Do not rush 1:30–2:30 and 4:00–5:00. "The AI never decides whether two records
  match" and "the AI writes rules, not answers" are the two sentences that set
  this apart. Everything else, plenty of people will have built.
- Terms to avoid on camera, and what to say instead: UTR → "reference number",
  ERP → "accounting software", TDS → "tax the customer held back", F1 →
  "accuracy", reconciliation → "matching things up", exception → "something that
  did not match".
