# Phase 55 — Atomic audit publication and post-link cleanup state

## Baseline and boundaries

- Parent SHA: 599e87e500a82587c76dea7c68625208ba852c7c
- Isolated branch: bot-bootstrap/phase-55-atomic-audit-publication
- Scope: local-agent audit durability and truthful status when POSIX no-overwrite publication succeeds but staging-alias cleanup fails.
- No changes to `main`; no pull request opened or merged.

## Findings addressed

1. Appending a JSONL record directly to the durable audit file could leave a truncated final record if a write failed after some bytes were written. That could make the hash-chain audit unreadable and permanently block future operations.
2. POSIX new-file publication is a two-step no-overwrite sequence: `link` publishes the destination and `unlink` removes the staging alias. A failure at the second step occurs after the destination has already appeared, so treating the whole helper as an unpublished failure is inaccurate.

## Changes

- Build the next audit version from one bounded, validated byte snapshot of the existing log plus the new event.
- Stage the entire next JSONL log in a unique same-directory temporary file; use a progress-checked write loop, flush/fsync, size/type checks, byte-for-byte reread verification, and full schema/hash-chain validation before publication.
- Publish the complete staged audit version through the same guarded atomic publication helper. A failed or partial staging write leaves the previous durable audit bytes unchanged.
- Keep event count, 2 MiB audit size bounds, 1,024-byte reserve for intent events, the audit lock, and explicit path/reparse checks.
- Have the publication helper report whether a new destination was published but its staging alias was not removed. The local write path records the publication as completed, retries cleanup in its finally block, and reports `staging_cleanup: failed` if an alias remains after retry rather than claiming no publication happened.
- Add deterministic tests for a staging write that succeeds partially and then raises, and for a successful POSIX link followed by one injected unlink failure.

## Guarantees and limits

- Before the atomic publication point, a failed staging write does not alter the current audit path.
- Existing audit bytes are parsed and hash-chain validated before being used as the prefix of the next staged version; the exact snapshot also supplies the expected hash/identity for guarded replacement.
- POSIX hard-link publication is still a two-system-call sequence. If destination link succeeds but temporary unlink fails, the destination is treated as published and cleanup is retried. If cleanup still fails, the result discloses the leftover temporary alias.
- Atomic replacement applies to a single path. It is not a multi-path transaction and does not provide a universal compare-and-swap against every non-cooperating process.
- Filesystem/permission failures can still prevent alias cleanup. In that case the destination is not deleted automatically.
- CI exercises deterministic injected failures; it does not prove behavior with every Windows filesystem, endpoint-security driver, or external writer on the user's PC.

## Acceptance

- Both foundation CI and Windows low-memory smoke must pass against the same final commit SHA.
- No `main` changes, PR creation, or merge.
