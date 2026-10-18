# Phase 38 — Temporary quarantine manifest write failures

## Scope

Repository: `araragijona-coder/botpyton`

Base SHA: `790b6c43bd164ddb86a030011eabc60b67ef7e49`

Branch: `bot-bootstrap/phase-38-quarantine-write-failure`

## Finding

The initial temporary-file quarantine had several manifest updates outside an exception guard and only handled `OSError` after a successful move. Storage-full errors, access-denied errors, and `ValueError` from validation could terminate the function without an honest report. The per-item writes also failed to stop later operations consistently. The session-level `status` stayed `in_progress` even when processing finished.

## Changes

- Handle `OSError` and `ValueError` for every manifest write after a quarantine session is created.
- Stop moving future files after any manifest persistence error; never call a later move with an untrustworthy recovery journal.
- Report actual successfully quarantined files separately from failures and skipped entries.
- Attempt one final manifest publication that records `completed` or `partial`, preserving a failure result in the returned report if an earlier persistence step failed.
- If the initial manifest cannot be persisted, no file has moved. Return an explicit failure and remove only an empty session directory where possible.
- If the start-audit event cannot be persisted, do not mutate files; persist `not_started` in the manifest if possible.

## External references

These are reliability analogues, not claims that upstream vendors experienced the exact same bug:

- [CCleaner Version History](https://www.ccleaner.com/ccleaner/version-history/): v7.12.1526 (2026-09-29) reports a file-write security fix and a shutdown timing fix; v7.11.1504 documents clearer per-file outcomes when Duplicate Finder cannot delete some files.
- [BleachBit 6.0.6](https://www.bleachbit.org/news/bleachbit-606) (2026-10-17): documents maintenance fixes around cleaning and processing multiple paths when one wipe path fails.
- [Python `os.replace` documentation](https://docs.python.org/3/library/os.html#os.replace): atomicity applies to an individual rename/replace, not the entire multi-step move-plus-manifest transaction.

## Regression tests

1. The initial manifest raises a simulated disk-full error: every source remains byte-identical, no quarantine move occurs, and the empty session directory is removed.
2. A manifest `ValueError` after the first successful move returns `manifest_write_failed`, reports one actual move, leaves the later file untouched, and preserves a readable partial manifest and restore candidate.
3. An access error while recording a changed-before-move skip stops subsequent candidates from moving.

## CI gate

Linux foundation tests and Windows low-memory smoke must both pass on the pull request. After merge, verify both workflows again on `main`. Tests use isolated temporary folders and do not change real user data.
