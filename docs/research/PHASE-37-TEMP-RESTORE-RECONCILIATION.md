# Phase 37 — Temporary quarantine restore recovery

## Scope

Repository: `araragijona-coder/botpyton`

Base: `6f95de677393a8b7ff3879280774c62f62edddb7`

Branch: `bot-bootstrap/phase-37-temp-restore-reconciliation`

## Finding

The general temporary-file quarantine workflow (separate from browser-cache quarantine) wrote `status="restoring"` before moving a file. After a successful move it set `status="restored"` and wrote the manifest without an exception guard. If that second write failed, the operation could throw instead of reporting that the file had moved. The durable manifest could remain `restoring`; a later preview saw no quarantine copy and reported it unavailable even when the original destination contained the recorded content.

## Changes

- Catch `OSError` and `ValueError` around each restore-manifest transition, and stop further moves whenever the recovery record cannot be persisted.
- Report successful file movements separately from metadata-only manifest reconciliation.
- A stale `restoring` entry is reconcilable only when the quarantine pathname is absent, the original destination is a regular file under a currently approved temporary root, and the recorded size and SHA-256 match after bounded hashing and stability checks.
- Keep the same explicit restore confirmation for reconciliation. The reconciliation changes only manifest metadata; it does not move/copy/replace/overwrite file content.
- Clean up temporary manifest staging files after write/replace errors without masking the original exception.
- Update the CLI so it presents reconciliation candidates and allows the user to explicitly confirm them even when zero files need moving.

## External references

These sources describe related reliability patterns, not the identical BOTPYTON bug:

- [BleachBit 6.0.6 release notes](https://www.bleachbit.org/news/bleachbit-606), published 2026-10-17: lists a fix so one failed CLI path does not prevent other submitted paths from being attempted, alongside cleaning and persistence fixes.
- [CCleaner version history](https://www.ccleaner.com/ccleaner/version-history/), v7.12.1526 dated 2026-09-29: file-write security fix and shutdown timing fix. v7.11.1504 also documents improved Duplicate Finder reporting of which files were actually removed instead of a generic error.
- [Microsoft MoveFileEx documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexa): the API returns success/failure and directs callers to extended error information on failure.

## Regression tests

1. Inject a manifest-write failure before the move and another after the move, plus failure of the final status write. Verify no exception escapes, the returned report counts the moved file, the original file bytes are correct, and the durable manifest remains inspectable.
2. Confirm a read-only preview reports `reconcile_manifest_state` only when the approved original matches the stored size and digest and the quarantine entry is absent.
3. With the separate explicit confirmation, reconcile the stale manifest without moving the file, and verify the final manifest item/status are `restored`.
4. Force atomic manifest publication to fail and verify the temporary staging file is removed while the previous manifest remains unchanged.

## Validation gate

- Linux foundation tests: PASS required.
- Windows low-memory smoke tests: PASS required.
- Verify the merge commit and repeat both CI checks on `main`.
- All regression data is generated under temporary test directories; no real user files are altered.
