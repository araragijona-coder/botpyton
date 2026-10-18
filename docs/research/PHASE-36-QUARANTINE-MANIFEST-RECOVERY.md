# Phase 36 — Quarantine manifest publication failure recovery

## Finding

In `quarantine_application_cache_candidates()`, a selected cache file may be moved to quarantine, then the manifest write may fail. The post-move handler previously caught only `OSError`. A `ValueError` (including manifest-bound validation) then flowed into the generic move-error branch, which assigned `state = "failed"` and could persist that false state even though the file had moved. The restore preview ignores `failed` items for normal restoration, so this mismatch could block the ordinary restore workflow.

## Fix

- Handle both `OSError` and `ValueError` as manifest-persistence errors after a successful movement.
- Do not reclassify an already moved file as a failed move.
- Stop further selected-file changes once manifest persistence fails, and report unprocessed selected files as skipped.
- Track the last successfully persisted manifest checksum and use it in partial reports.
- Catch final manifest-publication failures and preserve actual moved-file counters if another unexpected handled exception occurs.

## Regression coverage

The test makes the initial manifest write succeed and injects `ValueError` into both post-move and final writes. It verifies that the on-disk manifest remains valid and pending, the first selected file really is in quarantine, the second selected file remains untouched, preview still identifies the quarantined file for restore, and a subsequent explicit restore returns it without overwriting any file.

## External references

- Python [`os.replace()`](https://docs.python.org/3/library/os.html#os.replace): single-path replacement does not create a transaction across the separate file movement and manifest update.
- BleachBit [6.0.6 maintenance notes](https://www.bleachbit.org/news/bleachbit-606): a recent public example of fixing cleanup/configuration regressions and propagating failures when any artifact upload fails. This is a process lesson, not the same root cause.

## Limits

A persistent filesystem write failure cannot be made successful by software. The corrected behavior stops further moves, keeps the previous durable manifest available where possible, reports the actual partial state, and leaves the moved file for a later confirmed restore. Unit/CI fixtures do not replace validation on the user's Windows PC.
