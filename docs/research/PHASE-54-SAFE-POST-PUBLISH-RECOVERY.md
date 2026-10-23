# Phase 54 — Safe recovery after publication

## Baseline and boundaries

- Parent SHA: 6879b574f2e669119cae09d04a658dfa7f4ea7ea
- Isolated branch: bot-bootstrap/phase-54-safe-post-publish-recovery
- Scope: compensate only for failures that occur after a staged write has been published.
- No changes to main; no pull request opened or merged.

## Finding

A write may publish a complete staged file, then fail during destination verification or success-audit recording. The error can therefore occur after the destination has already changed. Blind rollback is unsafe because an external process may have changed the destination again in the meantime.

## Changes

- Capture the staged file's filesystem identity before publication and derive the expected post-publish target identity from that staged object.
- For replacements of existing files, retain the prior bounded byte content while validating the caller-provided expected SHA-256.
- If an error occurs after publication, attempt a compensating restore only when the target's path-chain identity, file identity, and SHA-256 still match the exact object/content BOTPYTON published.
- Write and verify the previous bytes to a second unique temporary, then publish through the same guarded atomic publication helper. No direct in-place overwrite fallback is introduced.
- If the destination no longer matches the expected published object/content or it cannot be inspected, skip rollback and preserve the observed external state. For a newly created file there is no prior version, so automatic deletion is deliberately not attempted.
- Record the recovery decision in the error audit detail and return a specific error explaining whether the prior version was restored or the operation was left for inspection.
- Add regressions for an external edit after publication, an audit failure after publication with safe restoration, and not deleting a new file that was externally changed.

## Guarantees and limits

- A rollback is attempted only when file identity/path-chain and content checks still match BOTPYTON's publication.
- If the checks cannot prove ownership, BOTPYTON refuses restoration instead of overwriting content that may belong to an external writer.
- The final guard still cannot be a universal compare-and-swap against every non-cooperating process; a last check-to-syscall race remains system-dependent.
- Restoring previous bytes is best effort. A filesystem/permission/handle error can prevent restoration, in which case inspect the target and audit log before retrying.
- Newly created files are not automatically deleted during compensation because this agent does not expose deletion as an operation.
- CI tests deterministic injected races and Windows execution behavior but does not prove safety against every filesystem, security product, or malicious process on the user's PC.

## Acceptance

Foundation CI and Windows low-memory smoke must both pass on the same final commit SHA. No main changes, PR creation, or merge.
