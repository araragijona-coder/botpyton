# Phase 51 — Handle-relative atomic file publication

## Baseline and constraints

- Parent SHA: bf616e23b49542da35eb3a89f4518bd64fef962c
- Isolated branch: bot-bootstrap/phase-51-atomic-file-publication
- No edits to main; no pull request or merge.

## Implementation

- Keep parent-directory handles open for the write transaction. POSIX opens a directory descriptor with no-follow semantics and validates that its device/inode/type match the captured directory identity.
- Create a unique staged file. On POSIX, create it with O_CREAT|O_EXCL relative to the open directory descriptor; on Windows, create a new temporary file in the same folder.
- Flush and fsync the staged file, then verify the staged bytes and size through the same file descriptor before closing it.
- POSIX publication is relative to the open directory descriptor: os.replace(..., src_dir_fd=..., dst_dir_fd=...) for replacement, or os.link(..., src_dir_fd=..., dst_dir_fd=...) followed by relative unlink for exclusive creation.
- Windows opens the staged file handle with DELETE access, rejects reparse-point/directory sources, and uses SetFileInformationByHandle(FileRenameInfo) with the already-open parent directory handle as RootDirectory. ReplaceIfExists is set only for a target which was present and passed the existing exact-hash checks.
- Read the published target back within the configured byte limit and confirm SHA-256. Keep current root permissions, audit sequence, exact expected hash, and no-auto-directory behavior.
- Add a POSIX regression that renames/replaces the directory path after opening its descriptor and verifies publication remains anchored to the original directory object. Existing create/replace tests exercise the Windows native handle-relative publication through Windows CI.

## Guarantees and boundaries

- Publication is atomic with respect to visibility when the underlying filesystem honors the native rename/link semantics. Readers should see either the previous file or the published file, not a partially written target. A staged file is written and verified before publication.
- A newly created target is exclusive: an existing name must not be overwritten.
- Handle-relative publication removes the final path re-resolution of parent-directory names at the instant of publication. It does not offer a portable compare-and-swap primitive tied to the previously read SHA-256 of an existing leaf. A separate external writer can still change that leaf after BOTPYTON's last hash check and before replacement.
- Windows may refuse publication because of sharing modes or filesystem/ACL behavior. The implementation fails rather than falling back to path-based replacement. POSIX platforms lacking required directory-relative operations also fail closed.
- If post-publication verification or audit recording fails, the call may report an error after the target has already changed. Inspect the target and audit log before retrying.
- These operations are not a sandbox against malicious processes running as the same user. CI uses temporary fixtures and does not validate the user's actual PC or directories.

## Acceptance

Both BotPyton foundation tests and BotPyton Windows low-memory smoke must pass at the exact final SHA. No PR is opened or merged by this phase.
