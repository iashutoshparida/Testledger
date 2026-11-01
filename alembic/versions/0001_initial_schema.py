"""Initial schema: projects, runs, test cases, results, quarantines.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("branch", sa.String(200), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "external_id", name="uq_runs_project_external"),
    )
    op.create_index("ix_runs_project_created", "runs", ["project_id", "created_at"])
    op.create_index("ix_runs_project_commit", "runs", ["project_id", "commit_sha"])
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_id", sa.String(500), nullable=False),
        sa.UniqueConstraint("project_id", "test_id", name="uq_tests_project_test"),
    )
    op.create_table(
        "results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "test_case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_id", "test_case_id", name="uq_results_run_test"),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error', 'skipped')", name="ck_results_outcome"
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_results_duration"),
    )
    op.create_index("ix_results_test_run", "results", ["test_case_id", "run_id"])
    op.create_table(
        "quarantines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "test_case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_quarantines_active",
        "quarantines",
        ["test_case_id"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_quarantines_active", table_name="quarantines")
    op.drop_table("quarantines")
    op.drop_index("ix_results_test_run", table_name="results")
    op.drop_table("results")
    op.drop_table("test_cases")
    op.drop_index("ix_runs_project_commit", table_name="runs")
    op.drop_index("ix_runs_project_created", table_name="runs")
    op.drop_table("runs")
    op.drop_table("projects")
