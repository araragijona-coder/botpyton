# Phase 39 — Fail closed on malformed quarantine filenames

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `94f9c9542faaf1c426f5412eac31130721315805`
- Scope: read-only restore preview and filesystem path validation.

## Defect found

The quarantine-manifest loader checks the manifest envelope and its checksum, but item-level validation happens as each entry is inspected. The check for `quarantine_name` required a string basename and rejected `.` / `..`, but did not reject an embedded NUL. Such a value cannot name a real file; a filesystem metadata operation on that path can raise `ValueError`. The item inspector caught `OSError` for `stat()`, but not `ValueError`, so a malformed manifest could make the restore preview terminate with an unexpected exception instead of reporting the entry as ineligible.

A checksum is an integrity-error detector, not proof that a manifest was made by a trusted writer. A locally edited manifest can have its checksum recomputed. The validation therefore must still reject impossible path values.

## Change

- Reject NUL characters in the quarantine filename before constructing/inspecting the filesystem path.
- Make low-level link/reparse checks fail closed for both `OSError` and `ValueError`.
- Do not loosen restore behavior, overwrite destinations, move files, or change cleanup scope.

## Regression

The test creates a legitimate quarantine session in a temporary directory, then writes a structurally valid and correctly checksummed manifest containing an embedded-NUL quarantine filename. The dry-run preview must return `preview_partial` with `skipped_unapproved_manifest_path`; it must not throw, restore, overwrite, or delete anything. It also confirms the originally quarantined bytes remain in the session.

## External maintenance research

These sources are used as design analogues, not as claims that either vendor had this exact defect:

- [BleachBit 6.0.6 official notes](https://www.bleachbit.org/news/bleachbit-606) (17 October 2026) describe continued work on path-processing reliability, skipping unreadable directories rather than aborting a scan, and preserving failure-tolerant operations across multiple paths.
- [CCleaner official Version History](https://www.ccleaner.com/ccleaner/version-history/) documents clearer per-file outcomes when Duplicate Finder cannot complete all file operations, instead of collapsing them into a generic error.

## Validation gate

Run the complete foundation suite on Linux and the full low-memory smoke workflow on Windows for the PR candidate. Merge only after both pass, then confirm both workflows pass on the resulting `main` SHA. All regression file operations use isolated temporary fixtures; this is not a real-PC validation.
