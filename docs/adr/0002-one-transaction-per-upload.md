# ADR 0002: One database transaction per upload

## Context
An upload writes a run, possibly hundreds of test cases and results, and quarantine changes.
A partial write (results without the quarantine update, or a run without its results) would make
the flaky report wrong and would block a retry, because the run id would already exist.

## Decision
Parse first, then do every write in one transaction (`db.transaction`). Re-uploads are made
idempotent with a content hash, so a CI retry after a timeout is safe.

## Consequences
Uploads are all-or-nothing, which a test verifies by injecting a failure after the inserts.
Very large reports hold one transaction for longer; the size limit (5 MB by default) bounds that.
