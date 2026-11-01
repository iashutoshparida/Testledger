import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from testledger.models import Base
from tests.conftest import alembic_config


def test_migration_matches_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_downgrade_removes_every_table(engine):
    command.downgrade(alembic_config(engine), "base")
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set()
    command.upgrade(alembic_config(engine), "head")


def _seed(conn):
    conn.execute(
        text("INSERT INTO projects (id, name, created_at) VALUES (1, 'p', CURRENT_TIMESTAMP)")
    )
    conn.execute(
        text(
            "INSERT INTO runs (id, project_id, external_id, commit_sha, branch, content_sha256,"
            " total, failures, created_at) VALUES (1, 1, 'r1', 'abc', 'main', 'h', 1, 0,"
            " CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text("INSERT INTO test_cases (id, project_id, test_id) VALUES (1, 1, 't')"))


def test_check_constraint_rejects_unknown_outcome(engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _seed(conn)
        conn.execute(
            text(
                "INSERT INTO results (run_id, test_case_id, outcome, duration_ms)"
                " VALUES (1, 1, 'maybe', 0)"
            )
        )


def test_partial_unique_index_allows_one_active_quarantine(engine):
    insert = text(
        "INSERT INTO quarantines (test_case_id, score, reason, quarantined_at, released_at)"
        " VALUES (1, 0.5, 'r', CURRENT_TIMESTAMP, :released)"
    )
    with engine.begin() as conn:
        _seed(conn)
        conn.execute(insert, {"released": "2026-01-01 00:00:00+00"})
        conn.execute(insert, {"released": "2026-02-01 00:00:00+00"})
        conn.execute(insert, {"released": None})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"released": None})


def test_foreign_keys_cascade_on_project_delete(engine):
    with engine.begin() as conn:
        _seed(conn)
        conn.execute(text("DELETE FROM projects WHERE id = 1"))
        assert conn.execute(text("SELECT COUNT(*) FROM runs")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM test_cases")).scalar_one() == 0
