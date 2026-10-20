# Phase 56 — Audit staging-alias cleanup disclosure

## Baseline and boundaries

- Parent SHA: 2c8b57bf15edcc3681555e7d60feef1d245e0160
- Isolated branch: bot-bootstrap/phase-56-audit-cleanup-disclosure
- Scope: disclose a post-publication cleanup failure in the local-agent audit journal.
- No changes to `main`; no pull request opened or merged.

## Finding

On POSIX, creating a new audit log publishes it with an exclusive hard link and then removes the temporary alias. If that first unlink failed and the cleanup retry in `_record()` also failed, the old code swallowed the second error and returned the audit record as though cleanup were complete. The destination audit record had in fact been published, but a temporary alias could remain without a caller-visible warning.

## Change

- Delay returning the new audit record until the staging cleanup attempt has completed.
- If the audit record was published but its staging alias still cannot be removed after the retry, raise a specific `LocalAgentError` stating that publication occurred and cleanup failed. Do not delete the published audit record or claim that publication was rolled back.
- Add a POSIX regression that injects two alias-unlink failures, verifies the call reports the post-publication condition, confirms the published audit chain is still valid, and confirms that the leftover alias is observable in the isolated fixture before test cleanup.

## Safety boundaries

- This does not make the audit journal a universal transaction and does not close races with non-cooperating local processes.
- The regression is specific to the POSIX hard-link publication path; Windows uses the handle-based rename path and receives its existing smoke coverage.
- A reported cleanup failure requires inspection before retrying, because the audit record may already be durably published.

## Acceptance

- Foundation unit tests and Windows low-memory smoke must pass against the same final commit SHA.
- `main` remains unchanged. No PR or merge.
