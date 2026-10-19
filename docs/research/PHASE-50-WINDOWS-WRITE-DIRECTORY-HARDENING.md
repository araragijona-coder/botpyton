# Phase 50 — Windows write and directory-operation hardening

## Baseline and scope

- Parent candidate: aa694c814b827f6053724ff45e3180b740272362
- Isolated branch: bot-bootstrap/phase-50-windows-write-directory-hardening
- Scope: bot/local_agent.py, local-agent regression tests, and security documentation.
- No changes to main; no pull request or merge.

## Implemented

- Add a Windows directory-chain lease that opens each existing directory component with FILE_FLAG_OPEN_REPARSE_POINT and FILE_FLAG_BACKUP_SEMANTICS, verifies directory/reparse attributes plus volume/file-index identity, and deliberately omits FILE_SHARE_DELETE while the operation is active.
- Use that lease during directory enumeration and file staging/publication. If a path changes before the lease is acquired, a component is a reparse point, native identity verification fails, or a Windows handle cannot be safely opened, fail closed.
- Check directory-chain identity after a listing and refuse to return data if the listed path was substituted while scanning.
- Verify staged writes by regular-file status, size, and SHA-256; after publication, read the target within its configured byte limit and verify its SHA-256 again.
- Preserve exclusive new-file publication, existing-target hash requirements, bounded I/O, root permissions, and audit events.
- Add regressions for a directory replaced during listing, a write whose parent is replaced before acquiring the lease, and a Windows-only test that verifies the directory handle blocks rename while held.

## Deliberate limitations

- The directory lease constrains ordinary directory rename/reparse substitution while a Windows operation is active. It does not pin the target file leaf through replacement.
- Replacing an existing file still uses path-based atomic replacement after validating the target identity and expected hash. A hostile process may still race the target leaf between the final check and publication; replacing a file through a stable handle-relative transaction is not implemented here.
- On non-Windows systems, path identity checks detect substitutions before/after the operation but do not hold kernel directory leases.
- If post-publication verification or audit recording fails, the operation can return an error after the target has already changed. Inspect the target and audit log before retrying.
- This is defense in depth, not a sandbox against a hostile process running as the same user. CI fixtures do not use personal files and do not verify behavior or resource use on the user's own PC.

## Acceptance

Both BotPyton foundation tests and BotPyton Windows low-memory smoke must pass at exactly the final candidate SHA. No PR is to be opened or merged as part of this phase.
