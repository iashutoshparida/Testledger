# API

Interactive docs: `/docs` (Swagger UI) and `/openapi.json` while the server runs.
Every endpoint except `/v1/health` needs an `X-API-Key` header. Every response carries
`X-Request-ID` (echoed from the request, or generated).

Errors always have the same shape:

```json
{"error": "RunConflictError", "detail": "run 'ci-1' already exists with different content", "request_id": "6f1c..."}
```

| Method & path | Purpose | Success | Errors |
|---|---|---|---|
| `GET /v1/health` | liveness + database check | 200 | 500 |
| `POST /v1/projects/{project}/runs?external_id=&commit_sha=&branch=` | upload a JUnit XML body | 201 new, 200 identical re-upload | 401, 409, 413, 422, 503 |
| `GET /v1/projects/{project}/runs?limit=&before_id=` | list runs, newest first, keyset paged | 200 | 401, 404, 422 |
| `GET /v1/projects/{project}/runs/{run_id}` | one run with its failing tests | 200 | 401, 404, 422 |
| `GET /v1/projects/{project}/flaky?only_flaky=` | verdict for every test with a failure | 200 | 401, 404 |
| `GET /v1/projects/{project}/tests/history?test_id=&limit=` | newest-first outcomes of one test | 200 | 401, 404, 422 |
| `GET /v1/projects/{project}/quarantine?active=` | quarantine episodes | 200 | 401, 404 |
| `DELETE /v1/projects/{project}/quarantine?test_id=` | release a test by hand | 204 | 401, 404 |

## Validation rules

* `project`: 1-100 chars, `[A-Za-z0-9._-]`, starting with a letter or digit.
* `commit_sha`: 7-64 hex characters.
* `external_id`: 1-200 chars of `[A-Za-z0-9._:/-]`.
* `branch`: 1-200 chars.
* Body: JUnit XML, at most `TESTLEDGER_MAX_UPLOAD_BYTES` (5 MB default). The server checks
  `Content-Length` and stops reading the stream as soon as the limit is crossed.

## Status codes

| Code | When |
|---|---|
| 401 | missing or wrong API key |
| 404 | unknown project, run or test; releasing a test that is not quarantined |
| 409 | same `external_id` re-uploaded with different content |
| 413 | report over the size limit |
| 422 | invalid parameters, malformed XML, DTD/entity declarations |
| 503 | server started without any API key configured |
| 500 | unexpected error; details are logged with the request id, never returned |

## Example

```bash
curl -X POST "http://localhost:8000/v1/projects/shop/runs?external_id=ci-42&commit_sha=abcdef1&branch=main" \
  -H "X-API-Key: $TESTLEDGER_API_KEY" -H "Content-Type: application/xml" \
  --data-binary @junit.xml
```

```json
{"run_id": 42, "created": true, "total": 812, "failures": 3,
 "quarantined": ["tests.test_checkout::test_payment_timeout"], "released": []}
```
