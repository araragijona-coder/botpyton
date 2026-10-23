# Phase 52 — Concurrent writes and expected-SHA conflicts

## Baseline and boundaries

- Parent SHA: b5b42bdfb13b6b8a920efd327fcd9ebaad5c2d4b
- Isolated branch: bot-bootstrap/phase-52-concurrent-write-conflicts
- No changes to main; no PR opened or merged.

## Changes

- Serialize all writes inside one configured root through a stable sidecar lock path derived from the normalized absolute root path.
- Re-check parent identity after obtaining the lock and evaluate current destination existence, regular-file status, and exact expected_sha256 while holding it. These decisions no longer rely on a hash captured before waiting for the lock.
- Two cooperative replacements that start with the same expected hash are serialized: the first valid publication can succeed; the next sees the changed hash and is rejected with an audited conflict.
- Concurrent creation of one previously missing path also has one winner. A later writer sees the newly existing target and fails closed instead of overwriting it without an expected hash.
- The sidecar lock is shared across cooperating threads/processes through the existing cross-platform exclusive_file_lock. It is stable and persistent to avoid split-lock races caused by deleting a lock file while another process has it open.
- Reserve the .botpyton-write-*.lock pattern: the local agent refuses reads/writes to it and omits it from directory listings.
- Add threaded race regressions for two replacements with the same expected hash, simultaneous new-file creation, and verifying that the internal lock is hidden and protected.

## Guarantees and limitations

- The serialization applies to BOTPYTON writers configured with the same normalized authorized root and using the same lock protocol. It serializes all writes in a root, including unrelated files, to favor correctness over write throughput for this bounded local agent.
- A failed stale-hash update is denied and audited; it does not replace the winner's contents.
- The lock is advisory/cooperative. External programs that do not acquire this lock can still modify the destination between BOTPYTON's final hash check and publication. The same-user hostile-process boundary described in prior phases remains.
- The lock file must not be removed while any process may be writing. It is intentionally persistent and hidden by this local-agent API; an external administrator/user can still modify or delete it.
- Atomic publication continues to use Phase 51 behavior: descriptor-relative on compatible POSIX systems; Windows uses a staged file handle and an absolute destination path due the observed RootDirectory compatibility issue. No universal compare-and-swap by content hash is claimed.
- A failure during post-publication verification or audit can still be reported after publication changed the file; inspect the target and audit trail before retrying.

## Acceptance

Both BotPyton foundation tests and BotPyton Windows low-memory smoke must pass on exactly the final SHA. No main changes, PR creation, or merge.
