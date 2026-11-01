# ADR 0001: A single outcome change is never called flaky

## Context
The first version flagged a test when its flip rate (share of consecutive runs whose outcome
changed) crossed a threshold. An integration test showed the flaw: with only 4 runs, a real
regression (pass, pass, fail, fail) has one flip in three transitions, a rate of 0.33, which
crossed the 0.30 threshold and quarantined a genuinely broken test.

## Decision
* A test with zero or one flip is never flaky: one change in behaviour is explained by one
  code change (a regression or a fix).
* A test that both passed and failed on the same commit is flaky regardless of flip rate,
  because nothing in the code changed.
* Otherwise, flag when the test has at least `min_runs` non-skipped runs, at least two flips
  and `max(flip rate, mixed-commit ratio) >= threshold`.

## Consequences
Quarantining a real regression hides a bug, which is worse than a late flaky detection, so the
rule prefers precision. The cost is recall on rarely-failing tests (p <= 0.1): see EVALUATION.md.
A property-based test checks that no single-step history is ever flagged.
