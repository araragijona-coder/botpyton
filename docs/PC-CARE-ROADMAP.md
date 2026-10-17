# BOTPYTON PC Care — implementation roadmap

## Phase 12 — Read-only local audit — implemented

- On-demand command: \`python -m bot.main --pc-check\`.
- Reports available physical memory, free disk space, selected temp/cache metadata and startup registration names.
- Uses the Python standard library and local OS information; no telemetry, AI/API calls, network update checks, package installation, service or scheduled task.
- No cleanup, process termination, registry changes, or updates are performed by this audit.

## Phase 13 — Process memory snapshot — implemented

- \`python -m bot.main --pc-processes\` and launcher option I.
- Uses Windows \`tasklist\` with a five-second timeout, no shell, no command-line capture; returns at most 30 processes sorted by working-set memory.
- Snapshot only. No process is terminated, reprioritized or modified.

## Phase 14 — Exact cleanup preview — implemented

- \`python -m bot.main --pc-cleanup-preview\` and launcher option J.
- Lists exact file paths, byte sizes, timestamps and age from a bounded set of approved roots.
- Preview is capped at 200 candidates. A partial scan is visibly marked and cannot be used for the Phase 16 execution path.
- Does not open file contents, move, or delete files. Browser profiles/caches, cookies, sessions, downloads, personal documents, and system directories are outside the executable cleanup scope.

## Phase 15 — Installed application inventory — implemented

- \`python -m bot.main --pc-apps\` and launcher option K.
- Reads application name, publisher, display version and install date from Windows uninstall registry metadata (default cap 500).
- Does not execute uninstallers, change registry data, or claim that an application is outdated based on inventory alone.

## Phase 16 — Reversible cleanup quarantine and restore — implemented on the development branch

### Run interactively

\`\`\`powershell
python -m bot.main --pc-cleanup-preview
python -m bot.main --pc-cleanup-quarantine
python -m bot.main --pc-cleanup-restore "C:\\path\\to\\BOTPYTON\\Quarantine\\session-...\\manifest.json"
\`\`\`

- Execution always performs a fresh scan; it accepts only a complete preview with at most 100 candidates.
- Before moving any file, it prints the exact path list and requires the exact phrase \`QUARANTINE OLD TEMP FILES\`. Any other input cancels without moving files.
- Eligible files are revalidated against an approved conventional temporary root, regular-file type, no symlink/reparse components, exact preview size/timestamp, and minimum age.
- Files are moved with \`os.replace\` to \`%LOCALAPPDATA%\\BOTPYTON\\Quarantine\\session-...\\\`; a JSON manifest is atomically written before and after each move.
- Restoration requires the exact phrase \`RESTORE BOTPYTON QUARANTINE\`, accepts only a manifest in a BOTPYTON quarantine session, validates original paths against approved temporary roots and never overwrites an existing original path.
- Cross-volume moves or files locked by a process may fail safely. Those files remain in place or in quarantine and are reported.
- No permanent deletion, registry edits, service installation, process termination, driver installation, or network/API calls are part of Phase 16.
- Tests use temporary folders only; no cleanup has been run on the user's PC. Windows CI still needs to pass for the candidate commit.

## Phase 17 — Explicit opt-in update checks — planned

Use only trusted, current sources such as Windows Update, Microsoft Store, winget metadata, or official hardware/software vendor sources. Run no network check without a separate opt-in; never install updates automatically in the initial release. Driver packages must not come from generic third-party driver packs.

## Phase 18 — Broader backup, restore and operation audit — planned

Extend recovery protection beyond temporary-file quarantine: validate backups, retain integrity hashes, produce durable audit records, and provide a dry-run restore checklist. The existing quarantine manifest is a first narrow recovery mechanism, not a general system backup.

## Phase 19 — Windows smoke tests, packaging and resource measurements — planned

Test supported Windows 10/11 configurations; measure idle/peak RAM, CPU and scan time on the actual Ryzen 5 5600G machine with only about 2 GB available; verify path/permission/locked-file edge cases; build a portable launcher and document Python/Windows minimums. The target remains on-demand, serial, bounded operation with no resident service.

## Safety and accuracy rules

- A cleanup estimate is not a guarantee that every byte can be reclaimed; some files may be locked or actively used.
- A startup item is not proof of performance impact. Do not disable an item based only on its name.
- A driver is not “obsolete” merely because its date is old. A reliable compatibility check and trusted source are required.
- Application inventory alone cannot establish that software is outdated or unnecessary.
- Bounded/partial scans are not exhaustive. Phase 16 refuses partial previews.
- Reports and manifests may contain local paths. Inspect them before sharing publicly.
- Development changes remain isolated on the development branch. No automatic merge into \`main\` and no test cleanup on the user's PC.

