# Phase 49 — Windows handle-backed bounded reads

## Baseline and constraints

- Parent candidate: `0e3cb0dd9a3fae037c8c20ddba80ca647f4a9bd5`
- Isolated branch: `bot-bootstrap/phase-49-windows-handle-access`
- No changes to `main`; no pull request or merge.
- Scope: `bot/bounded_io.py`, `tests/test_bounded_io.py`, local-agent guide, and audit notes.

## Implementation

- On Windows, open bounded-read inputs with native `CreateFileW`, `FILE_FLAG_OPEN_REPARSE_POINT`, and a conservative `FILE_SHARE_READ` share mode; inspect handle attributes before converting it to a Python binary stream.
- Reject a leaf carrying `FILE_ATTRIBUTE_REPARSE_POINT` or the directory attribute rather than following it as a read target.
- Read bytes from the already opened handle, then compare path/fstat signatures under the existing bounded-read contract.
- Keep the previous path-component identity checks around the local agent's read and write operations.
- Add a Windows-only regression that creates a symbolic link when permitted and confirms the bounded reader rejects the reparse-point leaf.

## Deliberate limitations

This reduces leaf-following and replacement races for bounded reads. It does not make arbitrary Windows paths atomic or eliminate parent-directory races. Existing handles may still have been opened before the reader; the caller must treat changing files as unsafe. Write publication still uses path-based operations with additional identity checks. Hostile same-user roots are unsupported.

## Acceptance

Both the foundation workflow and Windows low-memory smoke must pass at the exact candidate SHA. The test uses a disposable temporary directory; it does not read personal files. Actual target-PC permissions and resource use remain unverified until the user runs the app locally.
