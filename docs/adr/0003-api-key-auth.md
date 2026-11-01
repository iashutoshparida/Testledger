# ADR 0003: Hashed API keys, failing closed

## Context
Uploads come from CI machines, not people, so OAuth flows add little. Keys must never sit in plain
text in config, logs or the repository.

## Decision
The server stores only SHA-256 hashes of accepted keys (`TESTLEDGER_API_KEY_HASHES`) and compares
with `hmac.compare_digest`. If no keys are configured, protected endpoints return 503 instead of
silently allowing everything. The CLI reads the key from the environment, never from arguments,
so it does not appear in shell history or process listings.

## Consequences
Key rotation means adding a new hash, deploying, then removing the old one. There is no per-key
scoping yet; every key can write to every project.
