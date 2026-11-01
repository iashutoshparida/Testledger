# Contributing

1. Create a branch from `main`: `git switch -c feature/short-description`.
2. Install: `pip install -e ".[dev,postgres]"`.
3. Keep business logic in `service.py`, `flaky.py` or `junit.py`; routes only translate HTTP.
4. Schema changes need an Alembic revision; `test_migration_matches_models` fails otherwise.
5. Before pushing: `ruff check . && ruff format --check . && mypy && pytest --cov`.
   Run once with `TESTLEDGER_TEST_PG_URL` set if you touched queries or the schema.
6. One logical change per commit, with a message that says why, not only what.
7. Open a pull request; CI must be green before merge.
