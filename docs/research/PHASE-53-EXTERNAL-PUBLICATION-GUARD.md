# Phase 53 — External destination changes at publication

## Baseline and boundaries

- Parent SHA: af5f8cb34c56916f2b53df15e38113027b90c7c9
- Isolated branch: bot-bootstrap/phase-53-external-publication-guard
- The task is limited to the local-agent publication boundary.
- No changes to main; no pull request opened or merged.

## Finding

Phase 52 serializes cooperating BOTPYTON writers and rechecks expected_sha256 after staging. A remaining gap existed between the final path/hash checks in the writer and the native replace/link call. An external process could change the destination after the caller's last check but before the publication helper began.

## Changes

- Pass the expected existing-content SHA-256, captured path-chain identity, and per-root size limit into the publication helper.
- Revalidate the destination path identity and bounded content hash at the platform publication boundary. A detected change aborts before publication and preserves the external content.
- On Windows, open an existing destination with a handle that shares read and delete but not write while the last validation and native rename are performed. Incompatible existing writer handles or new write opens make the operation fail closed. Delete sharing remains necessary for rename-based replacement.
- Add deterministic regressions that inject an external in-place edit, a replacement of the target path, and a competing creator immediately before the publication helper is entered. The target's external bytes are asserted to survive.
- Update operator documentation with platform-specific guarantees and limits.

## Guarantees and limits

- Changes observable when the publication helper performs its final check are rejected; BOTPYTON does not replace that changed destination.
- New-file publication remains exclusive (link on POSIX; rename without replace on Windows), so a competing file creation cannot be overwritten.
- The final check is not a universal compare-and-swap. POSIX has no portable mechanism here to prevent an uncooperative process writing the same inode in the last check-to-syscall interval.
- On Windows, the no-write-share destination handle blocks incompatible open-for-write operations during the critical section when acquired successfully. FILE_SHARE_DELETE must remain enabled to support the atomic rename, so an external rename/replacement of the path can still race. A process with a compatible pre-existing mechanism outside the normal open-sharing protocol is not claimed to be sandboxed by this layer.
- A post-publication verification or audit failure can still be reported after the file changed; inspect the target and audit before retrying.
- CI validates the implementation and injected regressions, not the user's own folder ACLs, filesystem drivers, security software, or external writer patterns on their PC.

## Acceptance

Both foundation CI and Windows low-memory smoke must complete successfully against the same final commit SHA. No main changes, PR creation, or merge.
