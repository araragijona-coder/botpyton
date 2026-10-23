# Phase 46 — Local-agent folder permissions and audit

## Baseline and scope

- Repository: araragijona-coder/botpyton
- Base main: dd1652d00d45c0d6e73e1cd5ec099ee09244e7b4
- New module: bot/local_agent.py
- CLI surface: explicit --local-agent-* options
- No network listener, background service, merge, push, arbitrary shell, or project-code execution is added.

## Threat model

The operator configures a small set of real local folders and explicitly grants list/read/write. The agent treats path values as untrusted input even when they originate from its own CLI. It rejects path traversal, absolute/drive/alternate-stream forms, reserved Windows device names, symbolic links and detected Windows reparse points. Configured roots cannot overlap. The config and audit file cannot sit inside an authorized root, preventing an ordinary file operation from modifying the agent's own authority or audit target.

The baseline blocks common project control directories and names/extensions used for secrets. This is defense in depth, not a secret detector; users must choose narrow roots and grant the least permission needed.

## Operations

- List one directory level only, with a hard maximum of 500 entries and symlink/reparse entries skipped.
- Read UTF-8 files under a per-root size budget capped at 200,000 bytes.
- Create a new UTF-8 file exclusively, without overwriting a destination created concurrently.
- Replace an existing file only if the root has both read and write permissions and the caller supplies the exact current lowercase SHA-256. Use a temporary file and atomic replacement after re-checking the destination.
- No directory creation, deletion, arbitrary process execution, installation, privilege escalation, or network access.

## Audit contract

Each list/read/write request records an intent and a result in JSONL. Events include an increasing sequence, UTC timestamp, operation, root ID, relative path, outcome, bounded detail, optional byte count, previous event SHA-256 and their own SHA-256. The complete log is schema-checked and hash-chain-verified before append. Failed/corrupt/full audit blocks access. Content is not stored in audit events. Audit size is capped at 2 MiB and the event count at 5,000; there is no automatic rotation.

The hash chain detects many accidental or partial edits; it does not withstand a local administrator or an attacker who can rewrite both the application and log. Phase 48 captures device/inode/type identities for existing path components, rejects link/reparse components, checks that the chain remains stable after bounded reads, and rechecks parent/target identities around write publication. This detects path substitutions that persist across those checks, but the remaining path-based operations are not an atomic handle-relative sandbox: a narrow race can still exist between the final check and filesystem operation, especially on Windows. Do not use the agent with untrusted roots in a hostile same-user environment.

## Regression coverage

Temporary-directory tests cover configuration validation, non-overlapping roots, audit destination boundaries, read/list/write permissions, replacement hashes, traversal, protected names, symlinks when available, per-file limits, a simulated directory-identity swap during read, failed/tampered audit logs, and CLI/stdin behavior. The Windows low-memory workflow exercises the Windows code path where supported; neither CI workflow accesses the user's personal folders.

## Acceptance gate

Both BotPyton foundation tests and Windows low-memory smoke must pass on the phase branch commit. Do not merge automatically. Validate setup and memory use on a dedicated test folder on the target Windows PC before enabling real project roots.
