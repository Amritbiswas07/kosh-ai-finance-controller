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

> If you sell online, three systems track your money and none of them agree.
> Your accounting software says what you invoiced. Your payment provider says
> what it collected and paid out. Your bank says what actually arrived.
>
> They differ because the provider takes a fee, taxes that fee, holds money back
> for refunds, and pays out in batches days later. At month end somebody has to
> explain every rupee of that gap, and prove it to an auditor.
>
> And this is what they get to work with. Every payout carries a reference number
> tying it back — and by the time the bank prints it, it looks like this.
> Different case, glued to a company name, sometimes just missing.

---

## 0:30 – 1:20 · Watch it run

**SHOW** The browser. Tick **Model adjudication**, press **Reconcile**, let the
progress list run.

**SAY**

> This is Kosh. Three hundred and forty-five records from those three sources.
>
> The matching finishes in under a millisecond — about sixty-nine thousand
> records a second. What you're waiting on is the AI, and it runs exactly three
> times.
>
> The first thing you see isn't a score. It's where the money is. Six and a half
> lakh went out in batches. Five point seven nine reached the bank. Seventy-two
> thousand is still in flight.
>
> And this line has to be zero. If the maths doesn't hold, it says so here rather
> than quietly burying the difference.

---

## 1:20 – 2:20 · How it matches, and what the AI is for

**SHOW** The **What reconciled** table. Then open `src/kosh/match.py` and scroll
past the tier names.

**SAY**

> Matching happens in five passes, each only looking at what the pass before it
> couldn't solve.
>
> Easy ones first — both sides carry the same reference number. Then the same
> number written differently. Then the hard ones, with no shared reference at
> all, matched on amount and date. There it picks the best set of pairings across
> the whole file at once, rather than grabbing the nearest match row by row. That
> matters: grabbing as you go lets an early row take the partner a later row
> needed, so your answer changes if the file was sorted differently.
>
> Now the decision I'd defend hardest. **The AI never decides whether two records
> match.** That's pure arithmetic — it's the part an auditor checks, and "the AI
> thought so" is not an answer you can give them.
>
> The AI gets one job. A customer paid our bank directly, held back two percent
> as tax, and the bank mangled their name to `MERIDIAN LBS`. No rule catches
> that. A person reads it instantly as Meridian Labs — so does a small AI model.
> Then the arithmetic checks its answer before accepting it.

---

## 2:20 – 3:00 · Proving that was the right call

**SHOW** `outputs/ablation.md`.

**SAY**

> I didn't just decide that felt right. I let the AI loose on every part of the
> job first, then measured both ways.
>
> Identical accuracy from three AI calls as from eighteen. Everywhere else the
> leftovers have no match anywhere in the data — an invoice nobody paid, a payout
> the bank hasn't sent. Nothing to find, so anything it says is wrong. Out of
> fifteen tries it passed on thirteen, and the two guesses it made were caught by
> the arithmetic and thrown out.

---

## 3:00 – 3:45 · The part nobody likes showing

**SHOW** The **Exceptions** section. Filter to *Needs review*. Open one
`MISSING_IN_BANK` row.

**SAY**

> The brief asks for the match rate **and** what it couldn't sort out. This is
> that second half, and it's the harder one.
>
> Fourteen possible reasons something didn't match, and the list is fixed. No
> "other" bucket — that's where a tool like this hides its failures.
>
> Forty-two items need a human, worth two point four lakh between them. Each
> shows its evidence and what to do next.
>
> This one: forty-five thousand left the provider on the eighth and never reached
> the bank. That isn't cash — it's money you're owed, and someone can chase it
> today.

---

## 3:45 – 4:35 · How I know the numbers are real

**SHOW** `outputs/benchmark_llm.json`, then `docs/architecture.md`, section 7.

**SAY**

> One set of test data, written by the same person who wrote the matcher, proves
> nothing. So it rebuilds the whole dataset thirty times, each with a different
> mix of problems — ten thousand records. Perfect score on all thirty. Without
> the AI, 0.89 and 0.92. That gap is what the AI is worth.
>
> And I wrote down what went wrong. The scoring code caught a bug in my own data
> generator. And once the AI matched a bank interest payment to a customer
> invoice because the amounts lined up — fixed with a hard rule, not a better
> prompt.

---

## 4:35 – 5:00 · Close

**SHOW** `scripts/verify_offline.py` running through to PASS.

**SAY**

> All of this runs on this laptop. The AI model is free and open, loaded from
> disk — here it is with the internet switched off entirely. No API key, no cost
> per use.
>
> A hundred and twenty-five tests. Every accuracy number is checked against an
> answer key the program cannot read. And when it can't work something out, it
> says so.

---

## Notes

- 748 spoken words. At a normal 150 words a minute that is about 4:59.
  Counted from the script itself, not estimated — plain language takes more
  words to say the same thing, and the first draft of this rewrite ran 6:16.
- Don't rush 1:20–2:20. "The AI never decides whether two records match" is the
  point that sets this apart. Everything else, plenty of people will have built.
- Running long? Cut 3:45–4:35 down to just the thirty-datasets line.
- Read the numbers off your screen as you go, not off this page.
- Terms worth avoiding on camera, and what to say instead: UTR → "reference
  number", ERP → "accounting software", TDS → "tax the customer held back",
  F1 → "accuracy", reconciliation → "matching things up", exception →
  "something that didn't match".
