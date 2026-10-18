# Phase 42 — Validate checkpoint records before restore

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `da939d398683010ea92638abba38981f518ba7a5`
- Components: `bot/checkpoint.py`, `tests/test_checkpoint.py`

## Defect

`validate_checkpoint_state()` checked each record's payload, status, attempt count, dependency graph and idempotency fingerprint, but it did not require all fields consumed by the normal durable queue (including `created_at`, `updated_at`, `started_at`, `finished_at`, `last_error`, and `result`). An otherwise valid checkpoint could therefore be accepted and replace local memory, while the queue's record validator would reject that restored state immediately afterwards.

Also, `record.get("payload")` was passed to `validate_task()` without first verifying that it was a dictionary. A JSON `null` or another non-object payload could raise `AttributeError` rather than the public `CheckpointError` type. A SHA-256 digest only establishes that bytes match the digest, not that their schema is valid.

## Correction

- Require every durable record field and enforce types for status, timestamps, and optional string metadata.
- Require payload to be a JSON object before task policy validation.
- Enforce attempt limits and strict result/record JSON serialization before restore.
- Normalize malformed payload and dependency graph failures to `CheckpointError`.
- Preserve the existing write boundary: validate the entire checkpoint and manifest before replacing any existing queue state.

## Regression coverage

Tests export a legitimate queue state into a temporary directory, mutate its schema, recompute the manifest SHA-256, and then attempt restore into a separate destination containing preserved data. Cases cover a missing required timestamp and a non-object payload. Each must raise `CheckpointError` and leave the destination memory unchanged, demonstrating the validator is not merely catching checksum tampering.

## Official maintenance research

These releases are reliability analogues, not claims of identical upstream defects:

- [CCleaner official Version History](https://www.ccleaner.com/ccleaner/version-history/): v7.12.1526 (29 September 2026) describes a file-write security fix and several startup/shutdown/runtime crash fixes.
- [BleachBit 6.0.6 official release notes](https://www.bleachbit.org/news/bleachbit-606) (17 October 2026) describe maintenance fixes including saving preferences and repairing affected Windows configuration folders. Its release emphasizes avoiding a partially usable persisted configuration.
- [Python `os.replace` documentation](https://docs.python.org/3/library/os.html) documents replacement/rename behavior; this does not make application-level schema validation unnecessary.

## Validation gate

Run the full Linux foundation tests and Windows low-memory smoke on the PR candidate, merge only when both pass, and recheck both workflows on the final `main` SHA. All regressions use temporary test directories; this is not a test on the user's real PC.