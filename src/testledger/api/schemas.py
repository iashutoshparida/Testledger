"""Response models. Request inputs are path/query parameters validated in the routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IngestOut(BaseModel):
    run_id: int
    created: bool
    total: int
    failures: int
    quarantined: list[str]
    released: list[str]


class RunOut(BaseModel):
    id: int
    external_id: str
    commit_sha: str
    branch: str
    total: int
    failures: int
    created_at: datetime


class RunPage(BaseModel):
    items: list[RunOut]
    next_before_id: int | None


class FailureOut(BaseModel):
    test_id: str
    outcome: str
    duration_ms: int
    message: str | None


class RunDetailOut(BaseModel):
    run: RunOut
    failures: list[FailureOut]


class VerdictOut(BaseModel):
    test_id: str
    runs: int
    failures: int
    flips: int
    flip_rate: float
    mixed_commits: int
    score: float
    is_flaky: bool
    quarantined: bool
    reason: str


class HistoryOut(BaseModel):
    run_id: int
    commit_sha: str
    branch: str
    outcome: str
    duration_ms: int
    message: str | None


class QuarantineOut(BaseModel):
    test_id: str
    score: float
    reason: str
    quarantined_at: datetime
    released_at: datetime | None


class ErrorOut(BaseModel):
    error: str
    detail: str
    request_id: str
