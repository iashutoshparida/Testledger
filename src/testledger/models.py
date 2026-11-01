"""Relational schema.

projects 1--* runs 1--* results *--1 test_cases *--1 projects
test_cases 1--* quarantines (at most one active per test, enforced by a partial unique index)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    runs: Mapped[list[Run]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_runs_project_external"),
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index("ix_runs_project_commit", "project_id", "commit_sha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str] = mapped_column(String(64))
    branch: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    total: Mapped[int] = mapped_column(Integer)
    failures: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="runs")
    results: Mapped[list[Result]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("project_id", "test_id", name="uq_tests_project_test"),)
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    test_id: Mapped[str] = mapped_column(String(500))

    quarantines: Mapped[list[Quarantine]] = relationship(
        back_populates="test_case", cascade="all, delete-orphan"
    )


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint("run_id", "test_case_id", name="uq_results_run_test"),
        Index("ix_results_test_run", "test_case_id", "run_id"),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'error', 'skipped')", name="ck_results_outcome"
        ),
        CheckConstraint("duration_ms >= 0", name="ck_results_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    outcome: Mapped[str] = mapped_column(String(10))
    duration_ms: Mapped[int] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="results")


class Quarantine(Base):
    __tablename__ = "quarantines"
    __table_args__ = (
        Index(
            "uq_quarantines_active",
            "test_case_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    test_case: Mapped[TestCase] = relationship(back_populates="quarantines")
