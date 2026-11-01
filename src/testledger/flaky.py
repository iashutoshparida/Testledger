"""Flaky-test scoring.

A test is flaky when its outcome changes without the code changing. Two signals, both O(n):

* mixed commits: the same commit both passed and failed this test (e.g. a CI re-run).
  That cannot be explained by a code change, so it is the strongest evidence.
* flip rate: share of consecutive runs where the outcome switched pass<->fail.
  A real regression flips once and then stays failing, so its flip rate is ~1/(n-1).
  A test that fails at random with probability p flips at an expected rate of 2p(1-p).

score = max(mixed-commit ratio, flip rate), in [0, 1]. A test is flagged when it has enough runs and
either a mixed commit, or at least two flips with score >= threshold. Exactly one flip is never
flagged: one change in behaviour is explained by one code change. Skipped runs are ignored.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from testledger.junit import Outcome


@dataclass(frozen=True, slots=True)
class Observation:
    """One result of one test, in chronological order of its run."""

    commit_sha: str
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class Verdict:
    runs: int
    failures: int
    flips: int
    flip_rate: float
    mixed_commits: int
    commits_with_reruns: int
    score: float
    is_flaky: bool
    reason: str


def score_history(history: Sequence[Observation], *, min_runs: int, threshold: float) -> Verdict:
    if min_runs < 2:
        raise ValueError("min_runs must be at least 2")
    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold must be in (0, 1]")

    failed = [obs.outcome.is_failure for obs in history if obs.outcome is not Outcome.SKIPPED]
    commits = [obs.commit_sha for obs in history if obs.outcome is not Outcome.SKIPPED]
    runs = len(failed)
    failures = sum(failed)
    flips = sum(1 for prev, cur in pairwise(failed) if prev != cur)
    flip_rate = flips / (runs - 1) if runs > 1 else 0.0

    by_commit: dict[str, set[bool]] = {}
    counts: dict[str, int] = {}
    for sha, is_fail in zip(commits, failed, strict=True):
        by_commit.setdefault(sha, set()).add(is_fail)
        counts[sha] = counts.get(sha, 0) + 1
    rerun_commits = sum(1 for n in counts.values() if n > 1)
    mixed = sum(1 for outcomes in by_commit.values() if len(outcomes) == 2)
    mixed_ratio = mixed / rerun_commits if rerun_commits else 0.0

    score = round(max(flip_rate, mixed_ratio), 4)
    if runs < min_runs:
        is_flaky, reason = False, f"only {runs} non-skipped runs; need {min_runs}"
    elif mixed:
        is_flaky = True
        reason = f"passed and failed on the same commit ({mixed} commit(s))"
    elif flips <= 1:
        # One switch is fully explained by one code change (a regression or a fix),
        # however short the history is. Flakiness needs the outcome to come back.
        if failures and failed[-1]:
            is_flaky, reason = False, "consistent failure since one change: likely a regression"
        else:
            is_flaky, reason = False, "stable"
    elif score >= threshold:
        is_flaky = True
        reason = f"outcome flipped in {flips} of {runs - 1} consecutive runs"
    else:
        is_flaky, reason = False, "stable"
    return Verdict(
        runs=runs,
        failures=failures,
        flips=flips,
        flip_rate=round(flip_rate, 4),
        mixed_commits=mixed,
        commits_with_reruns=rerun_commits,
        score=score,
        is_flaky=is_flaky,
        reason=reason,
    )


def passing_streak(history: Sequence[Observation]) -> int:
    """Number of most-recent consecutive passes (skips ignored)."""
    streak = 0
    for obs in reversed(history):
        if obs.outcome is Outcome.SKIPPED:
            continue
        if obs.outcome is not Outcome.PASSED:
            break
        streak += 1
    return streak
