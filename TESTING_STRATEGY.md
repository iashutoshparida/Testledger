# Testing strategy

82 test cases: 35 unit, 47 integration. The 39 tests that touch a database run twice, on SQLite
and on PostgreSQL 16, giving 121 executions. Line + branch coverage is 98%; CI fails below 90%.
Warnings are errors (`filterwarnings = error`), which surfaced a stream leak in Starlette's
`BaseHTTPMiddleware` and led to a pure ASGI middleware.

| Layer | What is tested | How |
|---|---|---|
| JUnit parser (`test_junit.py`, 18) | all outcomes, nested suites, duplicate tests, truncation, malformed XML, XXE and entity-expansion attacks, size limit, bad durations | example-based, parametrised negative cases |
| Flaky scoring (`test_flaky.py`, 17) | stable, regression, fix, alternating, same-commit re-run, skips, minimum runs, invalid policy | example-based + 4 Hypothesis properties: score bounded in [0,1]; flips invariant under reversal; no single-step history is ever flagged; any same-commit pass+fail is always flagged |
| Migrations (`test_migrations.py`, 5) | Alembic schema equals ORM models; clean downgrade; CHECK constraint; partial unique index; FK cascade | against real databases |
| Service (`test_service.py`, 14) | idempotent and conflicting re-uploads, rollback when a step fails mid-transaction, quarantine and release, history window, keyset pagination, 1,203-test report crossing chunk boundaries | real database, failure injection with monkeypatch |
| API (`test_api.py`, 20) | auth (missing, wrong, empty key, no keys configured), validation errors per field, 413, 409, hostile XML, request ids, full flaky lifecycle over HTTP, 500 without leaking internals, OpenAPI | FastAPI TestClient |
| Evaluation + CLI (`test_evaluate_and_cli.py`, 8) | detector finds flaky tests and ignores regressions on a small seeded scenario; determinism; CLI exit codes for missing key, missing file, HTTP error, network failure | mocked `httpx.post` |

## Bugs the tests found during development

1. Short histories: a regression with one flip in three transitions crossed the 0.30 threshold and
   was quarantined. Fixed by requiring two flips ([ADR 0001](docs/adr/0001-flakiness-needs-two-flips.md)).
2. Alembic's configparser rejected PostgreSQL URLs containing `%` (URL-encoded socket path). Fixed
   by escaping. Found only because the suite also runs on PostgreSQL.
3. The partial unique index used a raw string `WHERE`, which works through Alembic but crashes
   `metadata.create_all`. Found by the evaluation harness. Fixed with `text()`.
4. `BaseHTTPMiddleware` leaked a receive stream after an unhandled exception (ResourceWarning).
   Replaced with a pure ASGI middleware.

## Running

```bash
pytest --cov
TESTLEDGER_TEST_PG_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ledger_test pytest
```
