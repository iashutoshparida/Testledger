import pytest
from sqlalchemy import func, select

from testledger.config import Settings
from testledger.errors import NotFoundError, ReportParseError, RunConflictError
from testledger.models import Result, Run, TestCase
from testledger.service import LedgerService


def report(**outcomes: str) -> bytes:
    """report(a="pass", b="fail") -> JUnit XML bytes."""
    cases = []
    for name, outcome in outcomes.items():
        body = {
            "pass": "",
            "fail": '<failure message="boom"/>',
            "error": "<error/>",
            "skip": "<skipped/>",
        }[outcome]
        cases.append(f'<testcase classname="suite" name="{name}" time="0.01">{body}</testcase>')
    return f"<testsuite>{''.join(cases)}</testsuite>".encode()


@pytest.fixture
def settings():
    return Settings(
        flaky_min_runs=4,
        flaky_threshold=0.3,
        release_after_passes=3,
        history_window=50,
        api_key_hashes="",
    )


@pytest.fixture
def svc(session_factory, settings):
    return LedgerService(session_factory, settings)


def ingest(svc, n, commit=None, **outcomes):
    return svc.ingest("shop", f"run-{n}", commit or f"{n:07x}", "main", report(**outcomes))


def test_ingest_creates_run_tests_and_results(svc, session_factory):
    res = ingest(svc, 1, a="pass", b="fail", c="skip")
    assert res.created and res.total == 3 and res.failures == 1
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(TestCase)) == 3
        assert s.scalar(select(func.count()).select_from(Result)) == 3


def test_reupload_of_same_run_is_idempotent(svc, session_factory):
    first = ingest(svc, 1, a="pass")
    again = ingest(svc, 1, a="pass")
    assert again.run_id == first.run_id and not again.created
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 1


def test_same_run_id_with_different_content_conflicts(svc):
    ingest(svc, 1, a="pass")
    with pytest.raises(RunConflictError):
        ingest(svc, 1, a="fail")


def test_parse_failure_writes_nothing(svc, session_factory):
    with pytest.raises(ReportParseError):
        svc.ingest("shop", "bad", "abc1234", "main", b"<testsuite>")
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0


def test_failure_mid_transaction_rolls_back_everything(svc, session_factory, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(LedgerService, "_update_quarantine", explode)
    with pytest.raises(RuntimeError):
        ingest(svc, 1, a="fail")
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0
        assert s.scalar(select(func.count()).select_from(TestCase)) == 0


def test_flaky_test_is_quarantined_and_regression_is_not(svc):
    pattern = ["pass", "fail", "pass", "fail", "pass"]
    newly = []
    for n, flaky_outcome in enumerate(pattern):
        regress = "pass" if n < 2 else "fail"
        newly += ingest(svc, n, flaky=flaky_outcome, regress=regress, stable="pass").quarantined
    assert newly == ["suite::flaky"]
    active = svc.quarantine_list("shop", active_only=True)
    assert [q.test_id for q in active] == ["suite::flaky"]
    report_ = {tv.test_id: tv for tv in svc.flaky_report("shop")}
    assert report_["suite::flaky"].quarantined
    assert not report_["suite::regress"].verdict.is_flaky
    assert "suite::stable" not in report_


def test_rerun_on_same_commit_with_different_outcome_quarantines(svc):
    for n in range(4):
        ingest(svc, n, t="pass")
    result = ingest(svc, 99, commit="0000003", t="fail")  # re-run of commit 0000003
    assert result.quarantined == ["suite::t"]


def test_quarantine_released_after_passing_streak(svc):
    for n, o in enumerate(["pass", "fail", "pass", "fail"]):
        ingest(svc, n, t=o)
    assert svc.quarantine_list("shop", True)
    released = []
    for n in range(4, 7):
        released += ingest(svc, n, t="pass").released
    assert released == ["suite::t"]
    assert svc.quarantine_list("shop", True) == []
    assert len(svc.quarantine_list("shop", False)) == 1


def test_manual_release_and_errors(svc):
    for n, o in enumerate(["pass", "fail", "pass", "fail"]):
        ingest(svc, n, t=o)
    svc.release("shop", "suite::t")
    with pytest.raises(NotFoundError):
        svc.release("shop", "suite::t")
    with pytest.raises(NotFoundError):
        svc.release("shop", "suite::missing")
    with pytest.raises(NotFoundError):
        svc.release("nope", "suite::t")


def test_keyset_pagination_and_run_detail(svc):
    ids = [ingest(svc, n, a="pass", b="fail" if n % 2 else "pass").run_id for n in range(5)]
    page1 = svc.list_runs("shop", limit=2, before_id=None)
    page2 = svc.list_runs("shop", limit=2, before_id=page1[-1].id)
    page3 = svc.list_runs("shop", limit=2, before_id=page2[-1].id)
    assert [r.id for r in page1 + page2 + page3] == sorted(ids, reverse=True)
    summary, failures = svc.run_failures("shop", ids[1])
    assert summary.failures == 1 and [f.test_id for f in failures] == ["suite::b"]
    with pytest.raises(NotFoundError):
        svc.run_failures("shop", 10_000)


def test_history_is_newest_first_and_bounded(svc):
    for n in range(6):
        ingest(svc, n, t="pass" if n % 3 else "fail")
    hist = svc.test_history("shop", "suite::t", limit=4)
    assert len(hist) == 4
    assert [h.run_id for h in hist] == sorted((h.run_id for h in hist), reverse=True)
    with pytest.raises(NotFoundError):
        svc.test_history("shop", "suite::zzz", limit=4)


def test_history_window_limits_what_the_scorer_sees(session_factory):
    svc = LedgerService(
        session_factory,
        Settings(flaky_min_runs=4, flaky_threshold=0.3, history_window=4, api_key_hashes=""),
    )
    outcomes = ["fail", "pass"] * 5 + ["pass"] * 4  # old flakiness, recent window all pass
    for n, o in enumerate(outcomes[:10]):
        ingest(svc, n, t=o)
    svc.release("shop", "suite::t")
    for n, o in enumerate(outcomes[10:], start=10):
        ingest(svc, n, t=o)
    [tv] = svc.flaky_report("shop")
    assert tv.verdict.runs == 4 and not tv.verdict.is_flaky


def test_large_report_crosses_chunk_boundaries(svc, session_factory):
    outcomes = {f"t{i}": ("fail" if i % 97 == 0 else "pass") for i in range(1203)}
    res = ingest(svc, 1, **outcomes)
    assert res.total == 1203 and res.failures == len([i for i in range(1203) if i % 97 == 0])
    res2 = ingest(svc, 2, **outcomes)
    assert res2.created
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(TestCase)) == 1203


def test_empty_project_report(svc):
    ingest(svc, 1, a="pass")
    assert svc.flaky_report("shop") == []
    with pytest.raises(NotFoundError):
        svc.flaky_report("ghost")
