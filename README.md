# TestLedger

A CI test-result ledger that stores every JUnit report in a relational database, finds flaky tests,
quarantines them automatically, and tells them apart from real regressions.

Made by Ashutosh Parida.

## Problem

Flaky tests (tests that pass or fail without any code change) erode trust in CI. Teams start
re-running pipelines until they go green, and real failures get ignored along with the noise.
The usual rule of thumb, "a test that failed more than once is flaky", is wrong in an important
way: a test broken by a real regression also fails repeatedly, so the rule hides real bugs.

TestLedger keeps the full pass/fail history of every test and scores flakiness from two signals:

* **Same-commit disagreement**: the test passed and failed on the same commit (for example, a CI
  re-run). Nothing in the code changed, so this is near-certain flakiness.
* **Flip rate**: how often the outcome switches between consecutive runs. A regression switches
  once and stays failing; a flaky test keeps switching.

A single switch is never called flaky, because one change in behaviour is explained by one code
change ([ADR 0001](docs/adr/0001-flakiness-needs-two-flips.md)).

## Results

Measured with `testledger evaluate --seeds 20`, which simulates CI histories with known ground
truth (flaky tests that fail with probability 5%-40%, regressions that start failing at a commit
and are sometimes fixed, 15% of commits re-run) and pushes every run through the real ingest path:
XML parser, database, quarantine policy. See [docs/EVALUATION.md](docs/EVALUATION.md).

| Metric | TestLedger | Naive rule (failed >= 2 times) |
|---|---|---|
| Precision | 1.000 | 0.554 |
| Recall | 0.975 | n/a |
| Real regressions flagged as flaky | 0 of 160 | 160 of 160 |
| Median runs until quarantine | 6.5 | n/a |

Misses are limited to tests that fail rarely (5 of 200 flaky tests, all with failure probability
of 10% or less). These are simulated histories, not production data.

## Features

* Hardened JUnit XML ingest (pytest, Maven Surefire, Gradle, jest-junit): size limit checked before
  parsing, DTD/entity attacks refused, every field validated.
* One transaction per upload; idempotent re-uploads by content hash; 409 on conflicting re-upload.
* Automatic quarantine and release after N consecutive passes; manual release endpoint.
* Relational schema with check constraints, foreign-key cascades, composite indexes and a partial
  unique index (one active quarantine per test). Alembic migrations.
* REST API with hashed API-key auth that fails closed, input validation, structured errors with
  request ids, keyset pagination, OpenAPI docs at `/docs`.
* CLI for CI pipelines: `testledger upload junit.xml --project ... --run-id ... --commit ...`.

## Tech stack

Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL / SQLite, Pydantic v2, defusedxml,
pytest, Hypothesis, ruff, mypy (strict), Docker, GitHub Actions.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# put the SHA-256 of your chosen key into TESTLEDGER_API_KEY_HASHES in .env:
python -c "import hashlib;print(hashlib.sha256(b'my-secret-key').hexdigest())"
alembic upgrade head
testledger serve            # http://127.0.0.1:8000/docs
```

Upload a report:

```bash
export TESTLEDGER_API_KEY=my-secret-key
testledger upload examples/pytest-report.xml --project shop --run-id ci-1 \
  --commit abcdef1 --branch main
```

With Docker and PostgreSQL:

```bash
echo "POSTGRES_PASSWORD=change-me" >> .env
docker compose up --build
```

## Using it in CI (GitHub Actions)

```yaml
- run: pytest --junitxml=junit.xml || true
- run: testledger upload junit.xml --url ${{ vars.TESTLEDGER_URL }} --project ${{ github.repository_owner }}-app
         --run-id ${{ github.run_id }}-${{ github.run_attempt }} --commit ${{ github.sha }} --branch ${{ github.ref_name }}
  env:
    TESTLEDGER_API_KEY: ${{ secrets.TESTLEDGER_API_KEY }}
```

## Testing

```bash
pytest --cov                                   # SQLite
TESTLEDGER_TEST_PG_URL=postgresql+psycopg://user:pw@localhost/ledger_test pytest   # + PostgreSQL
ruff check . && mypy
```

82 test cases (35 unit, 47 integration). The 39 database tests run on both SQLite and PostgreSQL,
121 executions in total, with 98% line and branch coverage. See
[TESTING_STRATEGY.md](TESTING_STRATEGY.md).

## Documentation

[ARCHITECTURE.md](ARCHITECTURE.md) · [API_DOCUMENTATION.md](API_DOCUMENTATION.md) ·
[TESTING_STRATEGY.md](TESTING_STRATEGY.md) · [SECURITY.md](SECURITY.md) ·
[CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) · [docs/adr](docs/adr)

## Limitations

* The evaluation uses simulated histories; real CI data has correlated failures (a broken shared
  fixture fails many tests at once) that the simulator does not model.
* Tests that fail very rarely (about 5% of runs) can take many runs to detect, or be missed,
  unless CI re-runs give same-commit evidence.
* One API key can write to every project; there is no per-project authorisation yet.
* The Docker image is defined but was not built in the development environment; CI builds it.

## Future work

Per-project API keys, a test-impact view (which files' changes precede failures), webhook
notifications when a test is quarantined, and a pytest plugin that skips quarantined tests.

## License

MIT
