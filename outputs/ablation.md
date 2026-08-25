# Does the model earn its place?

`Qwen/Qwen2.5-1.5B-Instruct` on mps, loaded in 5.1s. Same data, same seed, three configurations.

| configuration                          | link F1 | exc P | exc R | exc F1 | cleared | LLM calls | LLM s |
|----------------------------------------|---------|-------|-------|--------|---------|-----------|-------|
| deterministic only (no model)          |  0.8889 | 0.910 | 0.953 | 0.9313 |    82.4% |         0 |   0.0 |
| model on every leg                     |  1.0000 | 1.000 | 1.000 | 1.0000 |    84.2% |        18 |  62.4 |
| model on the two legs it can help (default) |  1.0000 | 1.000 | 1.000 | 1.0000 |    84.2% |         6 |  33.1 |

- **deterministic only (no model)** — adjudication verdicts: `{}`
- **model on every leg** — adjudication verdicts: `{'declined': 11, 'rejected_by_arithmetic': 4, 'accepted': 3}`
- **model on the two legs it can help (default)** — adjudication verdicts: `{'rejected_by_arithmetic': 3, 'accepted': 3}`
