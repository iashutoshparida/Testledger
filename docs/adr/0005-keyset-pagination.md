# ADR 0005: Keyset pagination for run listings

## Decision
`GET /runs` pages with `before_id` (`WHERE id < :before_id ORDER BY id DESC LIMIT :n`) instead of
`OFFSET`.

## Why
CI inserts runs continuously. With OFFSET, a new run shifts every page and clients see duplicates
or miss rows, and deep pages get slower because the database still walks the skipped rows.
Keyset pages are stable and use the primary key index.
