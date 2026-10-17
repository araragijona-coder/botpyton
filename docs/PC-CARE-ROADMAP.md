# BOTPYTON PC Care — implementation roadmap

## Phase 12 — Read-only local audit

**Branch:** \`bot-bootstrap/phase-12-pc-care-read-only\`  
**Goal:** give BOTPYTON a trustworthy, low-cost local diagnostic instead of alarm-style upsell messages.

### Included in this phase

- On-demand command: \`python -m bot.main --pc-check\`.
- Reports available physical memory and free disk space.
- Estimates old files in selected user temp/cache locations using metadata only (older than seven days), with an entry/depth cap.
- On Windows, lists startup registration names and source locations without exposing command-line arguments.
- Emits structured findings with severity labels, recommendations, and explicit unknown/not-checked states.
- Uses only Python's standard library and local OS information. No AI model, API key, telemetry, network query, package installation, startup service, or scheduled task is used.
- No files are deleted, no processes are stopped, no registry settings are changed, and nothing is updated.

### Important accuracy rules

- A cleanup estimate is not a promise that every byte can be reclaimed. Some files may be locked or needed by an active application.
- A startup entry is not proof of performance impact. The user must identify the publisher and purpose before disabling it.
- A driver is not “obsolete” merely because its date is old. Driver compatibility and a trusted Windows Update/vendor source are needed.
- The count of installed applications alone cannot establish that the PC is slow.
- A bounded scan may be partial; the report marks that condition and must not present itself as exhaustive.
- The report may contain local paths. Inspect it before posting it publicly.

### Next phases — not yet implemented

1. **Phase 13 — Process and startup review:** bounded snapshot of CPU/memory-heavy processes and startup publisher metadata; recommendations only, no termination or disabling.
2. **Phase 14 — Explicit cleanup preview:** calculate candidates first; display exact paths, age, file count and size; require a separate typed confirmation for each cleanup category; keep browser profiles, cookies, sessions, downloads, personal documents and system folders out of scope by default.
3. **Phase 15 — Update checks, opt-in:** use Windows Update, Microsoft Store, winget, or official vendor sources only on explicit user request; no third-party driver packs; never auto-install in the initial release.
4. **Phase 16 — Backups and restore:** create a manifest and reversible quarantine before any supported deletion; skip locked, linked, protected, or ambiguous paths.
5. **Phase 17 — Windows smoke tests and packaging:** verify on Windows 10/11, measure idle and peak RAM on the user's actual machine, build a portable launcher, and document the minimum supported Windows/Python versions.

Each phase must be separately tested. A selection to continue authorizes only the next listed phase; it does not authorize background services, automatic cleanup, driver installation, registry edits, or merges into \`main\`.
