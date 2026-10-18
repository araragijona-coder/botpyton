# Phase 40 — Validate required fields in browser-cache manifests

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `27d94a4edf584aa67a405f4ad28a4e896a5bb93c`
- Module: `bot/application_cache_quarantine.py`

## Defect

The bounded manifest loader validates path, file size, digest length, and state fields. The restore preview then formats several results by direct indexing into `item["browser"]`, `item["profile"]`, and `item["cache_area"]`. Those fields were not part of the schema validation. A malformed but correctly checksummed manifest missing one of these keys could raise an uncaught `KeyError` while producing a preview, instead of reporting invalid input. A checksum alone does not ensure the object has all required fields; it only verifies consistency with the digest that is present.

## Correction

The manifest validator now rejects any item whose browser/profile/cache-area metadata is missing, non-string, empty, or longer than 240 characters. The preview never reaches direct indexing with those required fields absent. No restore policy, root allowlist, overwrite behavior, or file movement is changed.

## Regression coverage

A test creates a legitimate cache-quarantine session entirely under a temporary directory. It removes each of the three fields one at a time, recomputes the canonical manifest checksum, and calls restore preview. Each malformed manifest must return `cache_manifest_invalid`, expose no ready items, and leave the quarantined bytes in place and the original path absent.

## External release-note research

These are analogous maintenance lessons, not claims of the same defect:

- [CCleaner official version history](https://www.ccleaner.com/ccleaner/version-history/): v7.12.1526 (29 September 2026) documents a file-write security correction and a startup crash fix; v7.11.1504 describes more precise reporting when some duplicate-file operations fail.
- [BleachBit 6.0.4 official notes](https://www.bleachbit.org/news/bleachbit-604) (8 September 2026) present a maintenance release focused on security, bug fixes, performance and cleanup improvements.

## Validation gate

Run the full Linux foundation tests and Windows low-memory smoke workflow on the PR candidate. Merge only when both pass, then verify both workflows on the final `main` SHA. All file mutation tests remain in isolated temporary folders; this is not a real-PC test.
