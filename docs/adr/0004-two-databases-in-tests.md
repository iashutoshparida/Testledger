# ADR 0004: Run every database test on SQLite and PostgreSQL

## Context
SQLite is fast for local development but differs from PostgreSQL in constraint enforcement,
partial indexes, window functions and transaction behaviour.

## Decision
The `engine` fixture is parametrised: every database test runs on SQLite and, when
`TESTLEDGER_TEST_PG_URL` is set, again on PostgreSQL. CI always sets it with a Postgres service
container. A migration test compares the Alembic schema with the ORM models on both databases.

## Consequences
The dual run caught a real bug: URL-encoded characters in a Postgres URL broke Alembic's config
parser. The suite takes about twice as long; it still finishes in under 30 seconds.
