# Phase 62 — write permissions, protected paths, and failure recovery

## Baseline and scope

- Parent SHA: cf07b2c227e4b95f3b6a136d7d61736db66a2072
- Isolated branch: `bot-bootstrap/phase-62-write-path-recovery-audit`
- Scope: local change-plan protected-path checks, staging-alias cleanup recovery, and local-agent regression coverage.
- No change to `main`; no pull request opened or merged.

## Findings and changes

### 1. Protected-path casing

The local change-plan validator compared protected prefixes and the exact `.cerebro/config.json` path case-sensitively. That policy was weaker than the local-agent baseline and could be bypassed by mixed-case names on case-insensitive filesystems. Protected prefix and exact-path comparisons now use `casefold()`; the configured allow-list of file suffixes and canonical relative-path rules remain unchanged.

Regression cases cover mixed-case variants of `.git/`, `.github/`, `.cerebro/config.json`, `.venv/`, `node_modules/`, `dist/`, and `build/`. The local-agent suite additionally confirms case-insensitive denial of protected components and sensitive suffixes and verifies those denials are recorded in the audit journal.

### 2. Failure after new-file publication

The local change-plan writer creates a new destination exclusively with a hard link from a staged file. If publication succeeded but removing the temporary staging alias failed, the helper raised before the caller recorded the destination in its rollback list. That could leave the new destination behind while the operation reported failure.

The writer now raises an explicit internal error when publication is known to have succeeded but alias cleanup failed. The transaction marks that exact destination as published, retries cleanup of the staging alias, and runs the existing conditional rollback checks. It removes or restores the destination only if path safety and the expected published SHA-256 still match; external changes are preserved. If cleanup or rollback still fails, the failure message includes that incomplete-recovery detail rather than claiming a complete rollback.

A fault-injection regression exercises the cleanup exception after successful publication and verifies that the created destination is rolled back and the temporary alias is removed when the cleanup retry succeeds.

### 3. Local-agent audit failure after publication

The local agent continues to treat a post-publication audit failure conservatively. For a replaced file, recovery may restore the previous bytes only while the destination still matches BOTPYTON's own published identity and SHA-256. For a newly created file, the agent does not automatically delete it after the post-publication audit failure: even if the contents currently match, avoiding a potentially external deletion is the conservative policy. It reports `new_file_left_in_place` and requires the operator to inspect the target and audit before retrying.

A new regression verifies that this condition is disclosed, the audit remains hash-chain-valid, and the file's state is not silently misrepresented. Existing tests continue to cover read-only root denial, expected-hash requirements for replacement, symlink/reparse-point rejection, concurrent changes, and audit corruption fail-closed behavior.

## Security and recovery limits

- The allowed root's configured permissions remain explicit: list/read/write are separate grants; replacing an existing local-agent file requires read and write plus the exact expected lowercase SHA-256.
- Symlink and Windows reparse-point components remain rejected on the guarded local-agent and local-change-plan paths.
- These checks are defense in depth and race detection, not a universal OS sandbox. The path-based local change-plan flow still has narrower protection against hostile same-user filesystem races than the local agent's directory-relative publication path.
- A rollback attempt can fail when a file is locked, a permission changes, a path is externally replaced, or a cleanup operation remains unavailable. BOTPYTON must preserve external versions and disclose unresolved cleanup/recovery instead of retrying blindly.
- CI runs against isolated temporary directories; it is not a test on the user's physical Windows PC. The target PC must still be validated using a dedicated test folder before authorizing real personal/project roots.

## Acceptance

- New mixed-case protected-path tests pass.
- The staged-publication failure regression confirms no unreported project-file mutation when cleanup can be recovered.
- Local-agent permission, symlink/reparse, audit, and post-publication failure regressions pass.
- Foundation CI and Windows low-memory smoke CI must both pass on the same final phase commit SHA.
- No PR or merge is authorized by this phase.
