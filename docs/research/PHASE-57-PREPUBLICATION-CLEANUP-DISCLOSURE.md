# Phase 57 — Pre-publication staging cleanup disclosure

## Baseline and boundaries

- Parent SHA: ca0152a8319b9539f151f1f4989b147e9751ea16
- Isolated branch: bot-bootstrap/phase-57-prepublication-cleanup-disclosure
- Scope: disclose temporary-file cleanup failures even when staging/publication itself fails first.
- No changes to `main`; no pull request opened or merged.

## Failure modes addressed

1. The audit journal could fail while writing/validating its staging file, then fail again while unlinking the staging alias. The original exception propagated without describing the remaining alias.
2. A normal local-file write could fail before publication, then fail to remove its staging file. Since no result object existed yet, the `finally` cleanup failure was silently discarded.

## Change

- Preserve the original pre-publication failure in the exception chain while adding a caller-visible staging-cleanup warning.
- Add a `published` flag to `AuditStagingCleanupError`; a pre-publication cleanup failure is explicitly marked `published=False`, while the existing post-publication warning remains distinct.
- If a failed local write has no result object and its staging cleanup also fails, raise a `LocalAgentError` that discloses both failures and chains the original error.
- Successful write results still report `staging_cleanup: failed` if the final alias cleanup fails after a result was prepared.
- Add isolated fault-injection regressions for both the audit log and a normal write. Each verifies that the destination was not published, the cleanup warning is exposed, and the remaining temporary alias is observable before the test removes it.

## Limitations

- These deterministic tests verify injected failure paths, not all Windows filesystem behavior, antivirus/file-locking interactions, or non-cooperating processes on the user's PC.
- A leftover staging file requires inspection before retrying. The code does not claim that a failed publication occurred.

## Acceptance

- Foundation tests and Windows low-memory smoke must pass on the same final commit SHA.
- No `main` changes, PR creation, or merge.
