from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from testledger.db import make_engine, make_session_factory

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    # configparser treats % as interpolation; URL-encoded hosts/passwords must be escaped.
    url = engine.url.render_as_string(hide_password=False).replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(engine: Engine, revision: str = "head") -> None:
    command.upgrade(alembic_config(engine), revision)


def _backends() -> list[str]:
    backends = ["sqlite"]
    if os.environ.get("TESTLEDGER_TEST_PG_URL"):
        backends.append("postgres")
    return backends


@pytest.fixture(params=_backends())
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """A freshly migrated database. Runs once on SQLite and, if configured, once on Postgres."""
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
        migrate(eng)
        yield eng
        eng.dispose()
        return
    url = os.environ["TESTLEDGER_TEST_PG_URL"]
    eng = make_engine(url)
    command.downgrade(alembic_config(eng), "base")
    migrate(eng)
    yield eng
    command.downgrade(alembic_config(eng), "base")
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)
