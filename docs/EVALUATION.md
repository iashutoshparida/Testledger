# Evaluation

Reproduce with `testledger evaluate --seeds 20 --out docs/evaluation_table.md` (about 2 minutes).

## Setup

Each seed simulates 150 commits on one branch, with 15% of commits re-run (as CI retries do).
Every run has 268 tests:

* 250 stable tests that always pass;
* 10 flaky tests that fail at random with probabilities 0.05, 0.05, 0.1, 0.1, 0.1, 0.15, 0.2, 0.2, 0.3, 0.4;
* 8 regressions that start failing at a random commit; half are fixed 3-14 commits later, half stay broken.

Each run is rendered as JUnit XML and sent through `LedgerService.ingest`, the same code path the
API uses, against a fresh SQLite database. A test counts as detected when the service quarantines
it. The baseline is the rule "a test that failed at least twice is flaky".

Default policy: `flaky_min_runs=5`, `flaky_threshold=0.30`, `history_window=50`.

## Results

Pooled over 20 seeds; 3467 CI runs ingested in total (268 tests per run).

| Metric | TestLedger | Naive rule (failed >= 2 times) |
|---|---|---|
| Tests flagged as flaky | 195 | 359 |
| Precision | 1.000 | 0.554 |
| Recall | 0.975 | n/a |
| Real regressions flagged as flaky | 0 of 160 | 160 |
| Median runs until quarantine (median of per-seed medians) | 6.5 | n/a |
| Flaky tests missed, by failure probability | p=0.05: 4, p=0.1: 1 | |

## Reading the results

* No real regression was quarantined in 160 cases. The naive rule flagged all 160, which is the
  failure mode that matters most: quarantining a regression hides a real bug.
* All 5 misses are rare flakes (p <= 0.1). With a 5% failure rate a test may fail only a handful of
  times in 150 commits, and never on a re-run commit.
* Detection is fast for frequent flakes because two flips within the first few runs already give a
  flip rate above 0.3.

## Threats to validity

* Simulated failures are independent. Real flaky tests often fail together (shared fixtures,
  overloaded runners), and real regressions sometimes fail intermittently.
* One branch only; merge queues and multiple branches are not simulated.
* The threshold was chosen before running the evaluation and not tuned on these seeds, but the
  simulator and the detector were written by the same person.
