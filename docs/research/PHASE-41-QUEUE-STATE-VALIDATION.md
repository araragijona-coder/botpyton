# Phase 41 — Validate persisted task queue state before use

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `bfd9a5da6c9e3f667283fd94189534401fb863f7`
- Components: `bot/queue.py`, `tests/test_queue.py`

## Defect

The JSON memory layer validates the top-level schema and bounds input size/depth, but the task queue assumes each persisted task record has all expected fields and every `depends_on` ID exists. For example, if a persisted task references a dependency absent from the records mapping, `claim_next()` and `_refresh_blocked()` can index the missing record and raise `KeyError`. A truncated or manually edited record can similarly surface as an internal `KeyError` rather than a clear queue validation error.

The JSON file can be syntactically valid and checksum-free; syntactic parsing alone is not application-level schema validation.

## Correction

- Validate every durable task record before queue operations use it.
- Require record fields, valid payload and task policy, matching payload ID, matching idempotency fingerprint, known state, bounded attempt count, and correctly typed metadata.
- Validate the whole dependency graph with the existing deterministic planner, rejecting unknown dependencies and cycles.
- Raise `QueueError` before any state update when persisted state is inconsistent. Do not silently repair, rewrite, retry, or discard ambiguous queue state.

## Regression coverage

Three isolated tests persist malformed state into a temporary directory and verify that claim/resume stops with `QueueError` and leaves the exact bytes unchanged: a missing record field, a correctly fingerprinted but unknown dependency, and a payload modified without updating its idempotency fingerprint. Existing concurrent-claim and task-transition tests remain in the full suite.

## External maintenance research

These sources are relevant reliability analogues, not reports of this exact BOTPYTON issue:

- [CCleaner official Version History](https://www.ccleaner.com/ccleaner/version-history/) documents fixes in v7.12.1526 (29 September 2026) for a file-write security issue, a startup crash, runtime-update handling, and shutdown timing; v7.11.1504 records clearer outcomes when some duplicate-file operations cannot complete. The common lesson is to validate assumptions and report precise, bounded failure states.
- [Python 3.9.20 release notes](https://www.python.org/downloads/release/python-3920/) describe a security bug-fix release that includes fixes involving malformed inputs and data-type confusion. This supports validating data contracts after parsing, without suggesting Python itself contains this BOTPYTON defect.
- [Python JSON documentation](https://docs.python.org/3/library/json.html) distinguishes decoding JSON syntax from application-level validation.

## Validation gate

Require both the Linux foundation test workflow and Windows low-memory smoke on the PR candidate. Merge only after both pass; then verify both workflows on the final `main` SHA. The tests use temporary files and do not inspect or modify the user's PC.