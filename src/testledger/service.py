"""Business logic. Knows nothing about HTTP; the API and CLI both call into this module."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from testledger.config import Settings
from testledger.db import transaction
from testledger.errors import NotFoundError, RunConflictError
from testledger.flaky import Observation, Verdict, passing_streak, score_history
from testledger.junit import Outcome, TestResult, parse_report
from testledger.models import Project, Quarantine, Result, Run, TestCase, utcnow

log = logging.getLogger("testledger.service")

_CHUNK = 500  # keeps IN (...) lists well under every driver's parameter limit
_FAILURE_OUTCOMES = (Outcome.FAILED.value, Outcome.ERROR.value)


@dataclass(frozen=True, slots=True)
class IngestResult:
    run_id: int
    created: bool
    total: int
    failures: int
    quarantined: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TestVerdict:
    __test__ = False

    test_id: str
    verdict: Verdict
    quarantined: bool


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: int
    external_id: str
    commit_sha: str
    branch: str
    total: int
    failures: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    run_id: int
    commit_sha: str
    branch: str
    outcome: str
    duration_ms: int
    message: str | None


@dataclass(frozen=True, slots=True)
class FailureEntry:
    test_id: str
    outcome: str
    duration_ms: int
    message: str | None


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    test_id: str
    score: float
    reason: str
    quarantined_at: datetime
    released_at: datetime | None


def _chunks(items: Sequence[str] | Sequence[int], size: int = _CHUNK) -> Iterable[list]:  # type: ignore[type-arg]
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


class LedgerService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings

    # ------------------------------------------------------------------ ingest
    def ingest(
        self, project: str, external_id: str, commit_sha: str, branch: str, report: bytes
    ) -> IngestResult:
        results = parse_report(report, self._settings.max_upload_bytes)
        digest = hashlib.sha256(
            b"\0".join([commit_sha.encode(), branch.encode(), report])
        ).hexdigest()
        failures = sum(1 for r in results if r.outcome.is_failure)

        with transaction(self._sessions) as session:
            proj = self._get_or_create_project(session, project)
            existing = session.scalar(
                select(Run).where(Run.project_id == proj.id, Run.external_id == external_id)
            )
            if existing is not None:
                if existing.content_sha256 != digest:
                    raise RunConflictError(
                        f"run {external_id!r} already exists with different content"
                    )
                log.info("duplicate upload ignored", extra={"run_id": existing.id})
                return IngestResult(existing.id, False, existing.total, existing.failures)

            test_ids = self._ensure_test_cases(session, proj.id, [r.test_id for r in results])
            run = Run(
                project_id=proj.id,
                external_id=external_id,
                commit_sha=commit_sha.lower(),
                branch=branch,
                content_sha256=digest,
                total=len(results),
                failures=failures,
            )
            session.add(run)
            session.flush()
            session.add_all(self._result_rows(run.id, test_ids, results))
            session.flush()
            quarantined, released = self._update_quarantine(session, run, test_ids, results)

        log.info(
            "run ingested",
            extra={"run_id": run.id, "project": project, "tests": len(results),
                   "failures": failures, "quarantined": len(quarantined)},
        )
        return IngestResult(run.id, True, len(results), failures, quarantined, released)

    def _get_or_create_project(self, session: Session, name: str) -> Project:
        proj = session.scalar(select(Project).where(Project.name == name))
        if proj is not None:
            return proj
        try:
            with session.begin_nested():  # savepoint: a concurrent insert must not kill our tx
                proj = Project(name=name)
                session.add(proj)
            return proj
        except IntegrityError:
            proj = session.scalar(select(Project).where(Project.name == name))
            assert proj is not None
            return proj

    def _ensure_test_cases(self, session: Session, project_id: int, names: list[str]) -> dict[str, int]:
        ids: dict[str, int] = {}
        for chunk in _chunks(names):
            rows = session.execute(
                select(TestCase.test_id, TestCase.id).where(
                    TestCase.project_id == project_id, TestCase.test_id.in_(chunk)
                )
            )
            ids.update({name: tc_id for name, tc_id in rows})
        missing = [TestCase(project_id=project_id, test_id=n) for n in names if n not in ids]
        if missing:
            session.add_all(missing)
            session.flush()
            ids.update({tc.test_id: tc.id for tc in missing})
        return ids

    @staticmethod
    def _result_rows(run_id: int, ids: dict[str, int], results: list[TestResult]) -> list[Result]:
        return [
            Result(
                run_id=run_id,
                test_case_id=ids[r.test_id],
                outcome=r.outcome.value,
                duration_ms=r.duration_ms,
                message=r.message,
            )
            for r in results
        ]

    # -------------------------------------------------------------- quarantine
    def _update_quarantine(
        self, session: Session, run: Run, ids: dict[str, int], results: list[TestResult]
    ) -> tuple[list[str], list[str]]:
        """Re-score only tests whose verdict can have changed: tests with any recent failure,
        plus tests already in quarantine. A test that has never failed cannot be flaky."""
        all_ids = list(ids.values())
        candidates: set[int] = set()
        for chunk in _chunks(all_ids):
            candidates.update(
                session.scalars(
                    select(Result.test_case_id)
                    .where(Result.test_case_id.in_(chunk), Result.outcome.in_(_FAILURE_OUTCOMES))
                    .distinct()
                )
            )
        active = self._active_quarantines(session, all_ids)
        candidates |= set(active)
        if not candidates:
            return [], []

        histories = self._histories(session, sorted(candidates))
        name_of = {tc_id: name for name, tc_id in ids.items()}
        quarantined: list[str] = []
        released: list[str] = []
        s = self._settings
        for tc_id in sorted(candidates):
            history = histories.get(tc_id, [])
            if tc_id in active:
                if passing_streak(history) >= s.release_after_passes:
                    active[tc_id].released_at = utcnow()
                    released.append(name_of[tc_id])
                continue
            verdict = score_history(history, min_runs=s.flaky_min_runs, threshold=s.flaky_threshold)
            if verdict.is_flaky:
                session.add(Quarantine(test_case_id=tc_id, score=verdict.score, reason=verdict.reason))
                quarantined.append(name_of[tc_id])
        return quarantined, released

    @staticmethod
    def _active_quarantines(session: Session, test_case_ids: list[int]) -> dict[int, Quarantine]:
        active: dict[int, Quarantine] = {}
        for chunk in _chunks(test_case_ids):
            for q in session.scalars(
                select(Quarantine).where(
                    Quarantine.test_case_id.in_(chunk), Quarantine.released_at.is_(None)
                )
            ):
                active[q.test_case_id] = q
        return active

    def _histories(self, session: Session, test_case_ids: list[int]) -> dict[int, list[Observation]]:
        """Last `history_window` results per test, oldest first. Run ids are monotonic,
        so they give a stable chronological order even when timestamps collide."""
        window = self._settings.history_window
        out: dict[int, list[Observation]] = defaultdict(list)
        for chunk in _chunks(test_case_ids):
            ranked = (
                select(
                    Result.test_case_id,
                    Run.commit_sha,
                    Result.outcome,
                    Run.id.label("run_id"),
                    func.row_number()
                    .over(partition_by=Result.test_case_id, order_by=Run.id.desc())
                    .label("rn"),
                )
                .join(Run, Run.id == Result.run_id)
                .where(Result.test_case_id.in_(chunk))
                .subquery()
            )
            rows = session.execute(
                select(ranked.c.test_case_id, ranked.c.commit_sha, ranked.c.outcome)
                .where(ranked.c.rn <= window)
                .order_by(ranked.c.test_case_id, ranked.c.run_id)
            )
            for tc_id, sha, outcome in rows:
                out[tc_id].append(Observation(sha, Outcome(outcome)))
        return out

    # ------------------------------------------------------------------ queries
    def _project(self, session: Session, name: str) -> Project:
        proj = session.scalar(select(Project).where(Project.name == name))
        if proj is None:
            raise NotFoundError(f"project {name!r} not found")
        return proj

    def _test_case(self, session: Session, project_id: int, test_id: str) -> TestCase:
        tc = session.scalar(
            select(TestCase).where(TestCase.project_id == project_id, TestCase.test_id == test_id)
        )
        if tc is None:
            raise NotFoundError(f"test {test_id!r} not found")
        return tc

    def list_runs(self, project: str, limit: int, before_id: int | None) -> list[RunSummary]:
        """Keyset pagination: stable under concurrent inserts, unlike OFFSET."""
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            query = select(Run).where(Run.project_id == proj.id)
            if before_id is not None:
                query = query.where(Run.id < before_id)
            runs = session.scalars(query.order_by(Run.id.desc()).limit(limit))
            return [
                RunSummary(r.id, r.external_id, r.commit_sha, r.branch, r.total, r.failures,
                           r.created_at)
                for r in runs
            ]

    def run_failures(self, project: str, run_id: int) -> tuple[RunSummary, list[FailureEntry]]:
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            run = session.scalar(select(Run).where(Run.id == run_id, Run.project_id == proj.id))
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            rows = session.execute(
                select(TestCase.test_id, Result.outcome, Result.duration_ms, Result.message)
                .join(TestCase, TestCase.id == Result.test_case_id)
                .where(Result.run_id == run.id, Result.outcome.in_(_FAILURE_OUTCOMES))
                .order_by(TestCase.test_id)
            )
            summary = RunSummary(run.id, run.external_id, run.commit_sha, run.branch, run.total,
                                 run.failures, run.created_at)
            return summary, [FailureEntry(*row) for row in rows]

    def flaky_report(self, project: str) -> list[TestVerdict]:
        s = self._settings
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            failing = list(session.scalars(
                select(Result.test_case_id)
                .join(TestCase, TestCase.id == Result.test_case_id)
                .where(TestCase.project_id == proj.id, Result.outcome.in_(_FAILURE_OUTCOMES))
                .distinct()
            ))
            if not failing:
                return []
            names = dict(session.execute(
                select(TestCase.id, TestCase.test_id).where(TestCase.id.in_(failing))
            ).all())
            active = self._active_quarantines(session, failing)
            histories = self._histories(session, sorted(failing))
            report = [
                TestVerdict(
                    names[tc_id],
                    score_history(histories[tc_id], min_runs=s.flaky_min_runs,
                                  threshold=s.flaky_threshold),
                    tc_id in active,
                )
                for tc_id in failing
            ]
        return sorted(report, key=lambda tv: (-tv.verdict.score, tv.test_id))

    def test_history(self, project: str, test_id: str, limit: int) -> list[HistoryEntry]:
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            tc = self._test_case(session, proj.id, test_id)
            rows = session.execute(
                select(Run.id, Run.commit_sha, Run.branch, Result.outcome, Result.duration_ms,
                       Result.message)
                .join(Run, Run.id == Result.run_id)
                .where(Result.test_case_id == tc.id)
                .order_by(Run.id.desc())
                .limit(limit)
            )
            return [HistoryEntry(*row) for row in rows]

    def quarantine_list(self, project: str, active_only: bool) -> list[QuarantineEntry]:
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            query = (
                select(TestCase.test_id, Quarantine)
                .join(Quarantine, Quarantine.test_case_id == TestCase.id)
                .where(TestCase.project_id == proj.id)
            )
            if active_only:
                query = query.where(Quarantine.released_at.is_(None))
            rows = session.execute(query.order_by(Quarantine.id))
            return [
                QuarantineEntry(name, q.score, q.reason, q.quarantined_at, q.released_at)
                for name, q in rows
            ]

    def release(self, project: str, test_id: str) -> None:
        with transaction(self._sessions) as session:
            proj = self._project(session, project)
            tc = self._test_case(session, proj.id, test_id)
            q = session.scalar(
                select(Quarantine).where(
                    Quarantine.test_case_id == tc.id, Quarantine.released_at.is_(None)
                )
            )
            if q is None:
                raise NotFoundError(f"test {test_id!r} is not quarantined")
            q.released_at = utcnow()
