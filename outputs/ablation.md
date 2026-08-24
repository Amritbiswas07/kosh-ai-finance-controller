# Does the model earn its place?

`Qwen/Qwen2.5-1.5B-Instruct` on mps, loaded in 7.0s. Same data, same seed, three configurations.

| configuration                          | link F1 | exc P | exc R | exc F1 | cleared | LLM calls | LLM s |
|----------------------------------------|---------|-------|-------|--------|---------|-----------|-------|
| deterministic only (no model)          |  0.8889 | 0.898 | 0.946 | 0.9217 |    84.4% |         0 |   0.0 |
| model on every leg                     |  1.0000 | 1.000 | 1.000 | 1.0000 |    86.1% |        18 |  46.4 |
| model on invoice→bank only (default)   |  1.0000 | 1.000 | 1.000 | 1.0000 |    86.1% |         3 |   9.3 |

- **deterministic only (no model)** — adjudication verdicts: `{}`
- **model on every leg** — adjudication verdicts: `{'declined': 13, 'rejected_by_arithmetic': 2, 'accepted': 3}`
- **model on invoice→bank only (default)** — adjudication verdicts: `{'accepted': 3}`
