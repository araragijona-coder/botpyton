# Phase 35 — Restore manifest reconciliation after persistence failure

**Status:** implementation prepared on an isolated branch; merge remains gated on Linux and Windows CI.

## Audit finding

During restore, BOTPYTON verifies the quarantined file and performs a no-overwrite move to the original browser-cache path. It then persists `item["state"] = "restored"`. If this manifest write fails, the file is already in place while the last durable manifest still records `moved` or `pending`. The next preview previously classified the absent quarantine file as `not_quarantined`, preventing guided recovery. In addition, `ValueError` from bounded manifest serialization was not caught by the restore persistence handler, allowing an outer handler to report zero restored files when a move had in fact succeeded.

## Changes

- Catch `OSError` and `ValueError` around per-item and final manifest writes.
- Track the last successfully persisted manifest digest, not an in-memory digest that may describe a failed write.
- Preserve actual file-movement and metadata-reconciliation counters on exceptional exits.
- Add a read-only reconciliation preview only when the quarantine entry is absent and the original destination is currently allowlisted, has safe parent components, is a regular unlinked file, and exactly matches the recorded byte length and SHA-256 under the existing byte budget.
- On explicit restore confirmation, recheck the same conditions and update only manifest state. No content is copied, moved, replaced or overwritten by reconciliation.
- Add regression tests injecting both `OSError` and `ValueError` during manifest publication.

## External engineering references

- Python's [`os.rename()` documentation](https://docs.python.org/3/library/os.html#os.rename) states that on Windows an existing destination raises `FileExistsError`. The restore implementation retains the no-overwrite move approach.
- [BleachBit 6.0.6 maintenance notes](https://www.bleachbit.org/news/bleachbit-606) describe fixes for real cleanup/configuration regressions and failures that could suppress work on other paths. This is an analogous maintenance practice, not the same defect.
- The [Python `os.fsync()` documentation](https://docs.python.org/3/library/os.html#os.fsync) explains flushing a file descriptor after a buffered stream's `flush()`; BOTPYTON's atomic manifest writer already uses this pattern before replacing the manifest.

## Validation

The regression tests verify that when the move succeeds but two successive manifest writes fail, BOTPYTON accurately reports one moved file, the original content stays intact, a new preview offers metadata reconciliation, and a second explicitly confirmed operation updates the manifest without moving the file again. The tests are to be confirmed by CI on both Linux and Windows before merge.

## Limitations

Size plus SHA-256 establish content equality, not which process placed the content at the path. The reconciliation is metadata-only and never overwrites an existing destination. A successful CI run does not replace testing on the user's Windows PC.
