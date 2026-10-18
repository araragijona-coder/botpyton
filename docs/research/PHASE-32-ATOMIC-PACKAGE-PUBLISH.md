# Phase 32 — preserve portable package/checksum consistency on publication failure

Date: 2026-10-18

## Confirmed issue

The portable package builder validated a temporary ZIP, replaced `BOTPYTON-portable.zip`, and only afterwards created and published `BOTPYTON-portable.zip.sha256`. If sidecar creation/publication failed (for example, a permissions error or a file held by another process), the command raised an error after already replacing the archive. An existing sidecar could then describe the previous ZIP rather than the newly published one.

This is a concrete partial-publication failure mode. It does not imply that every prior build left inconsistent outputs.

## Fix

- Validate and hash the staged ZIP before altering either final output.
- Write and flush the staged checksum sidecar before publishing anything.
- Keep any previous archive and sidecar as same-directory backups until both new files have been installed.
- If either replacement fails, remove only the files published by this attempt (identity checked) and restore prior outputs only when their destination remains unoccupied. If a concurrent change prevents rollback, retain the backup and report the incomplete rollback rather than overwriting the concurrent file.
- Add a regression test that injects failure exactly when publishing the checksum and verifies the old ZIP and sidecar remain byte-for-byte unchanged and consistent.

The two files are separate filesystem names and cannot be switched as a single atomic operation by `os.replace()`. The implementation guarantees staged validation and attempts rollback; it detects when concurrent changes prevent a safe rollback instead of silently overwriting them.

## External references

- Python's official `os.replace()` documentation describes atomic replacement for a single file/path, not a transaction spanning multiple destinations: https://docs.python.org/3/library/os.html#os.replace
- BleachBit 6.0.6 maintenance notes record a Windows deployment fix that makes the deploy fail if any upload fails, rather than checking only the last upload. That is not the same defect as BOTPYTON's package publication issue; it is a relevant upstream example of treating multi-artifact failure reporting consistently: https://www.bleachbit.org/news/bleachbit-606
- BleachBit 6.0.6 also documents reliability fixes around configuration permissions and startup, reinforcing the need to test failure paths rather than infer success from one completed step: https://www.bleachbit.org/news/bleachbit-606

## Verification requirement

The change must pass the full Linux test workflow and Windows low-memory smoke workflow on the PR head, followed by new green runs on the merged `main` SHA. CI validates source/package logic but does not substitute for measuring actual user-PC RAM or testing the desktop interface locally.
