Pooled over 20 seeds; 3467 CI runs ingested in total (268 tests per run).

| Metric | TestLedger | Naive rule (failed >= 2 times) |
|---|---|---|
| Tests flagged as flaky | 195 | 359 |
| Precision | 1.000 | 0.554 |
| Recall | 0.975 | n/a |
| Real regressions flagged as flaky | 0 of 160 | 160 |
| Median runs until quarantine (median of per-seed medians) | 6.5 | n/a |
| Flaky tests missed, by failure probability | p=0.05: 4, p=0.1: 1 | |
