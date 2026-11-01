# Architecture

## Layers

```
CI job ──(JUnit XML)──> CLI `testledger upload` ──HTTP──> FastAPI routes (api/app.py)
                                                              │  auth, validation, size limit,
                                                              │  error mapping, request ids
                                                              ▼
                                                    LedgerService (service.py)
                                       ┌──────────────┼───────────────┐
                                       ▼              ▼               ▼
                              junit.py parser   flaky.py scoring   SQLAlchemy models + Alembic
                              (pure, untrusted   (pure, O(n) per     (SQLite or PostgreSQL)
                               input)             test history)
```

Each layer depends only on the layer below it:

* `junit.py` and `flaky.py` are pure functions with no I/O. They are the most heavily tested parts,
  including property-based tests.
* `service.py` owns transactions and business rules. It knows nothing about HTTP, so the API, the CLI
  and the evaluation harness all reuse it.
* `api/app.py` translates HTTP to service calls. Domain errors map to status codes in one table
  (`_STATUS`), so routes contain no `try/except`.
* `create_app(settings, engine)` is a factory. Tests inject their own settings and a migrated
  database, so nothing reads global state.

## Data model

| Table | Purpose | Key constraints |
|---|---|---|
| `projects` | One per repository | `name` unique |
| `runs` | One CI job's report | `(project_id, external_id)` unique; indexes on `(project_id, created_at)` and `(project_id, commit_sha)` |
| `test_cases` | One row per distinct test id | `(project_id, test_id)` unique |
| `results` | One test's outcome in one run | `(run_id, test_case_id)` unique; `CHECK` on outcome and duration; index `(test_case_id, run_id)` for history scans |
| `quarantines` | Quarantine episodes | partial unique index: at most one row with `released_at IS NULL` per test |

All foreign keys cascade on delete. SQLite enforces them because `db.py` turns on
`PRAGMA foreign_keys` for every connection.

## Ingest flow (one transaction)

1. Parse the report outside the transaction. Bad input never opens a database transaction.
2. Get or create the project inside a savepoint, so a concurrent insert of the same project
   cannot abort the outer transaction.
3. If `external_id` exists: identical content (SHA-256 of commit, branch and report) returns the
   stored run (idempotent CI retries); different content raises `RunConflictError` (409).
4. Upsert test cases in chunks of 500 ids to stay under driver parameter limits.
5. Insert the run and its results.
6. Re-score only tests whose verdict can have changed: tests with a recorded failure, plus tests
   already quarantined. A test that has never failed cannot be flaky, so most tests are skipped.
7. Quarantine new flaky tests; release quarantined tests with `release_after_passes` straight passes.

Any exception rolls back all of it (`db.transaction`). A test injects a failure at step 7 and
checks that no run, test case or result was written.

## History queries

`_histories` uses `ROW_NUMBER() OVER (PARTITION BY test_case_id ORDER BY run_id DESC)` to fetch the
last `history_window` results per test in one query per chunk, served by the
`(test_case_id, run_id)` index. Run ids are monotonic, so ordering by id is stable even when two
runs share a timestamp.

## Pagination

`GET /runs` uses keyset pagination (`before_id`) rather than `OFFSET`, so pages stay consistent
while new runs are being inserted and the query cost does not grow with the page number.

## Decisions

See `docs/adr/` for the reasoning behind the scoring rule, the transaction boundary, auth design,
keyset pagination and the dual-database test setup.
