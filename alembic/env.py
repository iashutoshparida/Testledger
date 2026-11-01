"""Alembic environment: reads the database URL from TestLedger settings."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from testledger.config import get_settings
from testledger.db import make_engine
from testledger.models import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # tests pass an open connection
        _run(connection)
        return
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = make_engine(url)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
run_migrations_online()
