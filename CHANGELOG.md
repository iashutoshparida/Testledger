# Changelog

## 0.1.0

* JUnit XML ingest with size limits and XML attack protection.
* Relational schema with constraints, indexes and Alembic migration.
* Flaky scoring from flip rate and same-commit disagreement; one flip never counts as flaky.
* Transactional, idempotent uploads; automatic quarantine and release.
* REST API with hashed API keys, validation, keyset pagination and request ids.
* CLI: `upload`, `evaluate`, `serve`.
* Evaluation over 20 simulated CI histories: precision 1.00, recall 0.975, 0 of 160 regressions flagged.
* Docker, Compose with PostgreSQL, GitHub Actions on Python 3.11-3.13 with SQLite and PostgreSQL.
