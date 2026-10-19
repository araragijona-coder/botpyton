# Phase 48 — Local path identity race hardening

## Baseline and scope

- Parent candidate: `c3449981e7dc60e0c2544cf248eea64f840bb918`
- Isolated branch: `bot-bootstrap/phase-48-local-path-race-hardening`
- Scope: `bot/local_agent.py`, its regression tests, and threat-model documentation.
- No changes to `main`; no pull request or merge.

## Implemented

- Capture the identity of existing path components using device, inode/file-index, and file type; reject symlink/reparse components while capturing.
- After a bounded read, verify the path still resolves through the same component identities before returning any content.
- For writes, retain parent identity and, for replacement, the target identity; recheck them after staging and immediately before publication, in addition to the existing content-hash checks.
- Add a deterministic regression that swaps a directory during a read and verifies the operation fails closed rather than returning substituted content.

## Limits and acceptance

This is race detection and mitigation, not a complete filesystem sandbox. Python's portable path-based APIs do not provide a single atomic operation that binds all path components, validation, and publication across Windows and POSIX. A sufficiently timed local process can still race the final check; hostile same-user roots remain unsupported.

Acceptance requires both foundation tests and Windows low-memory smoke to pass at the exact candidate SHA. CI uses temporary test directories and does not inspect the user's personal files. Target-PC validation is still required before enabling real project roots.
