import pytest
from hypothesis import given
from hypothesis import strategies as st

from testledger.flaky import Observation, passing_streak, score_history
from testledger.junit import Outcome

P, F, E, S = Outcome.PASSED, Outcome.FAILED, Outcome.ERROR, Outcome.SKIPPED


def hist(outcomes, commits=None):
    commits = commits or [f"c{i}" for i in range(len(outcomes))]
    return [Observation(c, o) for c, o in zip(commits, outcomes, strict=True)]


def verdict(outcomes, commits=None, min_runs=5, threshold=0.3):
    return score_history(hist(outcomes, commits), min_runs=min_runs, threshold=threshold)


def test_always_passing_is_stable():
    v = verdict([P] * 20)
    assert (v.score, v.flips, v.is_flaky, v.reason) == (0.0, 0, False, "stable")


def test_regression_is_not_flaky():
    v = verdict([P] * 10 + [F] * 10)
    assert v.flips == 1
    assert not v.is_flaky
    assert "regression" in v.reason


def test_alternating_outcomes_are_flaky():
    v = verdict([P, F] * 6)
    assert v.flip_rate == 1.0 and v.is_flaky


def test_same_commit_pass_and_fail_is_flaky_even_with_low_flip_rate():
    outcomes = [P] * 9 + [F, P]
    commits = [f"c{i}" for i in range(9)] + ["c9", "c9"]
    v = verdict(outcomes, commits)
    assert v.mixed_commits == 1 and v.is_flaky
    assert "same commit" in v.reason


def test_error_counts_as_failure_and_skips_are_ignored():
    v = verdict([P, S, E, S, P, S, E, P, E])
    assert v.runs == 6 and v.failures == 3 and v.flips == 5


def test_short_history_regression_is_not_flaky():
    # 1 flip in 3 transitions = 0.33 > threshold, but one flip is one code change.
    v = verdict([P, P, F, F], min_runs=4, threshold=0.3)
    assert v.score > 0.3 and not v.is_flaky


def test_fixed_test_is_stable():
    v = verdict([F] * 5 + [P] * 5)
    assert not v.is_flaky and v.reason == "stable"


def test_needs_minimum_runs():
    v = verdict([P, F, P], min_runs=5)
    assert not v.is_flaky and "need 5" in v.reason


def test_empty_history():
    v = verdict([])
    assert (v.runs, v.score, v.is_flaky) == (0, 0.0, False)


@pytest.mark.parametrize("bad", [{"min_runs": 1}, {"threshold": 0.0}, {"threshold": 1.5}])
def test_rejects_invalid_policy(bad):
    kwargs = {"min_runs": 5, "threshold": 0.3} | bad
    with pytest.raises(ValueError):
        score_history([], **kwargs)


def test_passing_streak():
    assert passing_streak(hist([F, P, S, P, P])) == 3
    assert passing_streak(hist([P, P, F])) == 0
    assert passing_streak(hist([])) == 0


outcomes = st.lists(st.sampled_from([P, F, E, S]), max_size=60)


@given(outcomes)
def test_score_is_bounded(seq):
    v = verdict(seq)
    assert 0.0 <= v.score <= 1.0
    assert 0 <= v.flips <= max(v.runs - 1, 0)
    assert v.failures <= v.runs


@given(outcomes)
def test_flips_are_order_reversal_invariant(seq):
    assert verdict(seq).flips == verdict(list(reversed(seq))).flips


@given(st.integers(min_value=0, max_value=40), st.integers(min_value=0, max_value=40))
def test_single_step_change_never_flags_without_reruns(passes, fails):
    seq = [P] * passes + [F] * fails
    v = verdict(seq, min_runs=5, threshold=0.3)
    assert v.flips <= 1
    assert not v.is_flaky


@given(st.lists(st.sampled_from([P, F]), min_size=2, max_size=30))
def test_rerun_with_both_outcomes_always_flags(seq):
    if P not in seq or F not in seq:
        return
    v = verdict([*seq, P, F], [*(f"c{i}" for i in range(len(seq))), "x", "x"], min_runs=2)
    assert v.is_flaky
