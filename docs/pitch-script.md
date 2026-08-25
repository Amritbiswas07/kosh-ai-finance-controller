# Five-minute pitch — shot list and script

Track 4 asks for a public repo, a five-minute video and an architecture writeup.
This is the video. Plain language throughout — a judge should follow it whether
or not they've ever closed a set of books.

**Before you record**

```bash
./.venv/bin/kosh generate --seed 20260824     # build the data
./.venv/bin/kosh serve --port 8000            # leave this running
```

Have ready: a browser at `http://127.0.0.1:8000`, a second terminal in the repo,
`docs/architecture.md` open, and `outputs/ablation.md`. Pick dark or light and
stay in it. The numbers below match the current run — if you rebuild the data,
read the new ones off your screen.

---

## 0:00 – 0:30 · The problem

**SHOW** `data/bank_statement.csv` in the terminal. Scroll slowly through the
description column so people see the mess.

**SAY**

> If you sell online, three different systems keep track of your money, and they
> never quite agree.
>
> Your accounting software says what you invoiced. Your payment provider says
> what it collected and what it paid out to you. Your bank says what actually
> turned up in the account.
>
> The three don't match, because along the way the provider takes a fee, adds tax
> on that fee, holds some money back for refunds and disputes, and pays you in
> batches a couple of days later. So at the end of the month somebody has to sit
> down and explain, rupee by rupee, why the three numbers are different — and be
> able to prove it to an auditor.
>
> And this is what they have to work with. Every payout carries a reference
> number that should tie it back to the provider. But by the time your bank
> prints it, it's been squashed into a line like this — different case, stuck to
> a company name, sometimes missing altogether.

---

## 0:30 – 1:20 · Watch it run

**SHOW** The browser. Tick **Model adjudication**, press **Reconcile**, let the
progress list run.

**SAY**

> This is Kosh. Three hundred and forty-five records from those three sources.
>
> The matching itself finishes in under a millisecond — around sixty-nine
> thousand records a second. What you're waiting on is the AI, and it gets used
> exactly three times. I'll explain why in a moment.
>
> The first thing it shows you isn't a score. It's where your money actually is.
> Six and a half lakh rupees went out from the provider in batches. Five point
> seven nine lakh reached the bank. Seventy-two thousand is still on its way.
>
> And this line has to be zero. It's a self-check: if the maths doesn't hold, it
> says so right here instead of quietly hiding the difference.

---

## 1:20 – 2:20 · How it matches, and what the AI is for

**SHOW** The **What reconciled** table. Then open `src/kosh/match.py` and scroll
past the tier names.

**SAY**

> Matching happens in five passes. Each pass only looks at what the one before it
> couldn't solve.
>
> First, the easy ones — both sides carry the same reference number. Then the
> same number written differently. Then the hard ones, where there's no shared
> reference at all and it has to match on amount and date. For those it works out
> the best set of pairings across the whole file at once, rather than grabbing the
> nearest match one row at a time. That matters more than it sounds: grabbing as
> you go means an early row can take the partner a later row needed, and then your
> answer changes just because the file was sorted differently.
>
> Now the decision I'd defend hardest. **The AI never decides whether two records
> match.** That part is pure arithmetic, because it's the part an auditor will
> check, and "the AI thought so" is not an answer you can give them.
>
> The AI gets one job. A customer paid our bank directly instead of going through
> the provider, they held back two percent as tax, and the bank has mangled their
> name down to `MERIDIAN LBS`. No rule catches that. A person reads it instantly
> as Meridian Labs — and so does a small AI model. Then the arithmetic checks its
> answer before accepting it.

---

## 2:20 – 3:00 · Proving that was the right call

**SHOW** `outputs/ablation.md`.

**SAY**

> I didn't just decide that felt right. I let the AI loose on every part of the
> job first, then measured both ways.
>
> Exactly the same accuracy from three AI calls as from eighteen. Everywhere
> else, the leftovers are records that have no match anywhere in the data — an
> invoice nobody ever paid, a payout the bank hasn't sent yet. There's nothing
> for it to find, so anything it says is a wrong answer. Out of fifteen tries, it
> passed on thirteen, and the two guesses it did make were caught by the
> arithmetic check and thrown out.

---

## 3:00 – 3:45 · The part nobody likes showing

**SHOW** The **Exceptions** section. Filter to *Needs review*. Open one
`MISSING_IN_BANK` row.

**SAY**

> The brief asks for the match rate **and** the things it couldn't sort out. This
> is that second half, and honestly it's the harder one.
>
> There are fourteen possible reasons something didn't reconcile, and that list
> is fixed. There's deliberately no "other" category, because that's exactly
> where a tool like this hides its failures.
>
> Forty-two items need a human, and together they account for two point four lakh
> rupees. Every single one shows its evidence and what to do next.
>
> This one: forty-five thousand rupees left the provider on the eighth, and no
> matching credit has arrived at the bank. So that isn't cash yet — it's money
> you're still owed. That's something a finance person can pick up and chase.

---

## 3:45 – 4:35 · How I know the numbers are real

**SHOW** `outputs/benchmark_llm.json`, then `docs/architecture.md`, section 7.

**SAY**

> One set of test data, written by the same person who wrote the matcher, on the
> same afternoon, proves nothing. So it rebuilds the entire dataset from scratch
> thirty times over, each one with a different mix of problems. Ten thousand
> records in total. It scores a perfect one point oh on all thirty. Without the
> AI it's 0.89 and 0.92 — that gap is what the AI is actually worth.
>
> And I wrote down what went wrong. The scoring code caught a bug in my own data
> generator. A stress test showed accuracy falling off a cliff, and that turned
> out to be my own maths counting empty results as failures. And once, the AI
> matched a bank interest payment to a customer's invoice, because the amount
> happened to line up. I fixed that with a hard rule, not a better prompt.

---

## 4:35 – 5:00 · Close

**SHOW** `scripts/verify_offline.py` running through to PASS.

**SAY**

> All of this runs on this laptop. The AI model is free and open, loaded from
> disk — here it is running with the internet switched off completely. No API
> key, no cost per use, and any result can be rebuilt from scratch.
>
> A hundred and twenty-five tests. Every accuracy number is checked against an
> answer key the program is built so it cannot read. And when it can't work
> something out, it tells you.

---

## Notes

- About 730 words. At a normal speaking pace that's roughly 4:50, which leaves
  room to breathe.
- Don't rush 1:20–2:20. "The AI never decides whether two records match" is the
  point that sets this apart. Everything else, plenty of people will have built.
- Running long? Cut 3:45–4:35 down to just the thirty-datasets line.
- Read the numbers off your screen as you go, not off this page.
- Terms worth avoiding on camera, and what to say instead: UTR → "reference
  number", ERP → "accounting software", TDS → "tax the customer held back",
  F1 → "accuracy", reconciliation → "matching things up", exception →
  "something that didn't match".
