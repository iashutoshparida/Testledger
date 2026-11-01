# Security

## Threat model
Reports are uploaded by CI machines and must be treated as untrusted. API keys are the only
credential. The database may be shared with other services.

## Controls
* **XML attacks**: `defusedxml` with DTDs, entities and external references forbidden. Billion-laughs
  and XXE payloads are rejected with 422; both have tests.
* **Resource exhaustion**: size limit enforced from `Content-Length` and again while streaming the
  body, before any parsing; test ids capped at 500 characters; failure messages truncated to 2,000.
* **Authentication**: only SHA-256 hashes of keys are configured; comparison with
  `hmac.compare_digest`; no keys configured means 503, never open access.
* **Secrets**: the CLI reads the key from `TESTLEDGER_API_KEY`, never from arguments. `.env` is
  git-ignored; `.env.example` holds no secrets. Compose refuses to start without a Postgres password.
* **SQL injection**: all queries go through SQLAlchemy bound parameters; no string-built SQL.
* **Error leakage**: unexpected exceptions return a generic 500 with a request id; the stack trace
  is logged server-side only (tested).
* **Container**: runs as a non-root user.
* **Data integrity**: CHECK constraints, unique constraints and foreign keys enforced in the
  database, not only in Python.

## Known gaps
No per-project authorisation, no rate limiting, no key expiry. Put the service behind TLS
termination; it does not serve HTTPS itself.

## Reporting
Please open a private security advisory on the repository rather than a public issue.
