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
- Eligible files are revalidated against the canonical per-user Windows `%LOCALAPPDATA%\\Temp` root, regular-file type, no symlink/reparse components, exact preview size/timestamp, and minimum age. If `TEMP` is misconfigured to a personal folder, it is not accepted for execution.
- Files are moved with \`os.replace\` to \`%LOCALAPPDATA%\\BOTPYTON\\Quarantine\\session-...\\\`; a JSON manifest is atomically written before and after each move.
- Restoration requires the exact phrase \`RESTORE BOTPYTON QUARANTINE\`, accepts only a manifest in a BOTPYTON quarantine session, validates original paths against approved temporary roots and never overwrites an existing original path.
- Cross-volume moves or files locked by a process may fail safely. Those files remain in place or in quarantine and are reported.
- No permanent deletion, registry edits, service installation, process termination, driver installation, or network/API calls are part of Phase 16.
- Tests use temporary folders only; no cleanup has been run on the user's PC. Windows CI still needs to pass for the candidate commit.

## Phase 17 — Explicit opt-in application update listing — implemented on the development branch

- Run \`python -m bot.main --pc-update-check\` or choose N in the Windows launcher. Nothing runs on startup.
- Uses the fixed per-user WindowsApps WinGet alias, verifies exported source metadata, and proceeds only when the source is named \`winget\`, marked Trusted, has type \`Microsoft.PreIndexed.Package\`, and points exactly to \`https://cdn.winget.microsoft.com/cache\`.
- Runs only \`winget list --upgrade-available --source winget --disable-interactivity\` with a 30-second timeout, no shell, and bounded output. The code does not call \`winget upgrade\`, \`winget install\`, \`--all\`, or any installer.
- This explicit check may use the network; the report says so. It never installs/upgrades applications and does not accept package/source agreements on the user's behalf.
- If WinGet, source metadata, source trust or the configured endpoint cannot be validated, the check fails closed. A listing is evidence from WinGet, not a guarantee that every installed app is represented.
- Windows system updates, Microsoft Store updates, and device drivers remain unchecked by this phase. Do not use generic third-party driver packs.



## Phase 18 — Recovery integrity, dry-run restore and operation audit — implemented on the development branch

- Each quarantine manifest records the SHA-256 digest and nanosecond mtime of every candidate and a checksum over the manifest itself, revalidated before restore previews or restore execution. Candidate and aggregate content hashing is bounded to 512 MiB per run and uses 256 KiB chunks, not full-file buffering.
- Restore checks the quarantined file against its recorded byte count and SHA-256 digest immediately before moving it. Missing, oversized, changed or tampered files are skipped and remain in quarantine.
- \`python -m bot.main --pc-cleanup-restore-preview "PATH\\manifest.json"\` validates session structure, hashes and original destination availability without moving or writing any file.
- \`python -m bot.main --pc-audit\` reads the bounded local operation ledger. Start/end records store operation, counts and status but intentionally omit exact file paths and contents. Each JSON record has a SHA-256 checksum.
- Audit read is bounded to 200 displayed records, 5,000 scanned directory entries, 16 KB per record and 2 MB total; malformed/modified records are surfaced as integrity errors.
- Quarantine and restore do not proceed if their audit-start record cannot be written. Audit finalization failures are included in operation reports.
- SHA-256 event checksums detect accidental changes but are not a cryptographic signature against a person able to rewrite both the file and checksum. Local audit is not a backup or a remote log.
- Regression tests cover manifest integrity, tampering, restore preview/no-overwrite, audit verification and checksum failure. CI must pass for this branch SHA before calling the phase verified. No test has changed files on the user's PC.

## Phase 19 — Portable source package and one-pass resource benchmark — implemented in CI; real-machine measurement pending

- \`python -m bot.main --package-portable OUTPUT_DIR\` builds \`BOTPYTON-portable.zip\` and an adjacent SHA-256 sidecar. The archive uses fixed member ordering/timestamps and excludes tests, Git metadata, local memory state, generated reports, virtual environments and caches.
- Package manifest lists every packaged source file with byte count and SHA-256. \`python -m bot.main --verify-portable-package ZIP_PATH\` checks ZIP CRCs, path traversal/duplicate/symlink members, archive-size caps, manifest membership, and every member hash. Package creation/verification use only the standard library.
- This is a portable Python source archive, not a bundled interpreter or standalone EXE. Python 3.11+ is required; optional GitHub/developer worker features can need external tools and local authentication.
- CI builds and verifies the archive on Ubuntu and Windows and publishes a short-retention artifact for inspection/download.
- \`python -m bot.main --pc-benchmark\` (menu option Q) runs one bounded read-only pass: system profile, startup inventory, top-10 process memory snapshot, installed-app metadata capped at 100, and cleanup preview capped at 30 candidates / 1,000 entries per root. It reports wall/CPU time per operation and current/lifetime-peak resident memory where the OS exposes it. It runs no network/update command and performs no cleanup.
- Benchmark measurements are a one-pass snapshot, not a CCleaner comparison. CI confirms code/packaging behavior; it cannot measure the user's own Ryzen 5 5600G with approximately 2 GB available. Actual idle/peak RAM and timings remain pending until the package is executed on that PC.
- The launcher remains on-demand; no service, scheduled task, background monitor, API or telemetry was added.



### Remaining release validation — requires the target PC

- Measure actual idle/peak RAM, CPU and elapsed scan times on the Ryzen 5 5600G with around 2 GB free; compare the same tasks/settings against CCleaner if a performance claim is desired.
- Verify supported Windows 10/11 versions beyond the current windows-latest runner; validate real permission-denied, locked-file, long-path and cross-volume behavior on that machine.
- The current portable artifact is a source ZIP, not a bundled interpreter or standalone installer/EXE. A native packaged executable is a separate future decision and must be measured for size/startup/RAM before adoption.
- Keep on-demand, serial, bounded operation. No resident service, scheduled task, API, telemetry, cleanup, or update check is enabled automatically.

## Phase 20 — Bounded read-only storage analysis — implemented on the development branch

- Run \`python -m bot.main --pc-storage "C:\\Users\\YOUR_USER\\Downloads"\` or choose R in the launcher. The path must be an existing folder explicitly selected by the user; nothing scans the full drive automatically.
- Reports total bytes observed, file/directory/entry counts, largest files and a bounded file-extension breakdown. It never opens file contents and makes no deletion recommendations.
- Default bounds: 5,000 directory entries, depth 6, top 20 files and a five-second elapsed-time deadline. Hard maxima are 20,000 entries, depth 8, top 50 files and 10 seconds.
- Symlinks and Windows reparse points are skipped; a linked root is rejected. Access errors, depth/entry caps and timeouts are reflected in the partial status, so partial sizes cannot be mistaken for a full-drive total.
- The result contains local file paths. No network, registry changes, file moves, deletions or settings changes occur.
- Automated tests cover totals, ordering, limits, depth, invalid roots and symlink refusal. CI must pass before treating this phase as verified; real disk timing still depends on the target PC.

## Phase 21 — Bounded SHA-256 duplicate-file inventory — implemented on the development branch

- Run \`python -m bot.main --pc-duplicates "C:\\Users\\YOUR_USER\\Downloads"\` or choose S in the launcher. The user selects one folder; nothing scans an entire drive automatically.
- Uses file-size grouping before hashing; only same-size candidates are read. SHA-256 is calculated in 256 KiB chunks, with a default 32 MiB per-file cap, 256 MiB aggregate hash budget, 2,000-hash cap, 5-second deadline and 5,000-entry metadata scan.
- Hard scan limits: 20,000 entries, depth 8, top 25 groups, 10 seconds max. Oversized files, path/access failures, hash-budget exhaustion or timeouts make the result partial rather than implying a complete scan.
- Refuses a linked/reparse root; skips linked/reparse descendants; verifies file size and nanosecond mtime before and after hashing. The output groups identical SHA-256 values and gives a potential duplicate-byte estimate only.
- No file is moved, renamed, changed, or deleted. File contents are read locally to hash them but never sent over a network. The output lists local paths.
- Automated tests cover identical vs same-size non-identical content, hash/file budgets and symlink-root rejection. CI must pass before treating this phase as verified.

## Phase 22 — Local Windows device-driver inventory — implemented on the development branch

- Run \`python -m bot.main --pc-drivers\` or choose T in the launcher.
- Uses the standard Windows PowerShell binary from \`%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\` and a fixed, non-interactive local \`Get-CimInstance Win32_PnPSignedDriver\` query. It does not accept paths, queries, or script fragments from user input.
- Reads at most 201 metadata records from the provider and returns at most 200: device name/class, manufacturer, driver provider/version/date, signature state and INF name. It omits device IDs and serial-like identifiers from the report.
- Fifteen-second timeout, fixed field/output caps, no shell, no network access, no installation/update/deletion/disable action. Non-Windows systems report unsupported.
- Inventory metadata is not a verdict that a driver is obsolete. No vendor catalog comparison or driver package recommendation is made by this phase.
- Regression tests cover platform gating, the fixed query shape, no-shell timeout, bad-output handling and record limits. The Windows CI smoke exercises the real inventory command; behavior on the user's PC still needs local validation.

## Safety and accuracy rules

- A cleanup estimate is not a guarantee that every byte can be reclaimed; some files may be locked or actively used.
- A startup item is not proof of performance impact. Do not disable an item based only on its name.
- A driver is not “obsolete” merely because its date is old. A reliable compatibility check and trusted source are required.
- Application inventory alone cannot establish that software is outdated or unnecessary.
- Bounded/partial scans are not exhaustive. Phase 16 refuses partial previews.
- Reports and manifests may contain local paths. Inspect them before sharing publicly.
- Development changes remain isolated on the development branch. No automatic merge into \`main\` and no test cleanup on the user's PC.



## Phase 23 — Local Windows disk-status snapshot — implemented on the development branch

- Run python -m bot.main --pc-disk-health or select **U** in BOTPYTON.bat.
- Queries the fixed local Windows PowerShell executable with fixed commands: Get-PhysicalDisk and Get-CimInstance -ClassName Win32_DiskDrive. No user text is interpolated into the script.
- Returns status/operational status where exposed, media/bus/interface type and size metadata. Separate provider lists avoid implying that the same drive is necessarily represented identically by both providers.
- Caps each provider at 100 returned records (requests 101 to detect a cap), output at 500 KB, field lengths at 200 characters and execution at 15 seconds. No shell, network, repair, formatting, optimization, update or disk modification.
- The report explicitly says this is not a full S.M.A.R.T. attribute report. Missing, Unknown, OK, or Healthy status is not a guarantee that a disk has no fault; hardware/bridge/controller support varies.
- Automated tests cover non-Windows gating, query shape, output normalization, record caps and timeout/invalid-output handling. The Windows smoke workflow exercises the command. Real-device coverage and timing on the target PC remain unverified until locally run.


## Phase 24 — Read-only doctor and concrete next steps — implemented on the development branch

- Run `python -m bot.main --doctor` or choose **V** in `BOTPYTON.bat`.
- Checks Python >=3.11, required BOTPYTON files, available physical RAM, free disk space, Git discovery and the standard Windows PowerShell path.
- Reports `ready`, `caution` or `blocked`; every warning/block includes an actionable next step. Missing core files explicitly instruct the operator to restore/re-extract a known-good checkout rather than inventing replacements.
- Low RAM advises stopping heavy work and avoiding parallel workers; low disk space warns against arbitrary deletion. No tools are launched, no packages are installed, no network/API calls occur, no files/settings change.
- Automated tests inject platform/resources for deterministic coverage of ready, old-Python, low-RAM, missing-file, low-disk and missing-root outcomes. Windows CI invokes the real preflight command.
- Exit code 0 means ready or caution; exit code 2 means a blocking prerequisite was detected. A successful preflight is not a promise that later commands cannot fail.


## Phase 25 — Fail-closed operation results and postcondition checks — candidate; CI pending; partial-measurement status fix included in follow-up commit

- PC-care reports for local audit, cleanup preview, process/application/update inventory, storage/duplicate scans, driver/disk inventory, benchmark, restore preview and quarantine/restore pass through a shared result guard.
- The guard checks report structure, known status, provider return code, declared returned counts versus actual records, audit integrity, scan limits, truncated output, access errors and skipped actions.
- Recognized partial reports exit with code 2; failed reports or failed postconditions exit with code 1; unknown report/status exits with code 3. Only reviewed success statuses exit 0.
- No automatic retry occurs. Unknown or partial outcomes tell the operator what evidence to inspect. The guard assesses report consistency, not hardware health or completeness of Windows provider coverage.
- Unit tests cover success, partial scan, provider failure, count mismatch, integrity error, unknown status and malformed report.


## Phase 26 — Competitor capability audit — research baseline recorded

- Added `docs/CCLEANER-PARITY-AUDIT.md` with a source-linked comparison of BOTPYTON's implemented scope against current official CCleaner product/support documentation.
- The audit explicitly records feature gaps rather than claiming equivalence: broad application/browser cleaning, startup control, app/driver updates, performance optimizer, scheduled/real-time operation, full uninstaller, disk wipe and hardware monitoring remain absent or incomplete.
- Future parity work must preserve BOTPYTON's constraints: on-demand startup, no AI/API calls, bounded serial work, explicit user consent, reversible file handling and truthful partial/error reporting. Any feature that needs network access must be separately opted in and documented.
- This is a documentation/research checkpoint, not proof of comparative speed, quality, safety or RAM use. Benchmark comparisons require the target PC and identical task scopes.


## Phase 27 — Read-only startup target advisor — candidate; CI pending

- Added `--pc-startup-advisor` and launcher option **W**. Reads bounded HKCU/HKLM Run/RunOnce values and Startup-folder items; caps output at 200 records.
- Extracts only an executable/script token from recognized command formats, never returns command arguments, and reports target present/missing/unresolved. Startup shortcuts remain explicitly unresolved because the tool does not invoke shell/COM to resolve them.
- Does not disable entries, execute commands, change registry/settings, access the network, or claim boot impact from a name. It reports local paths and warns that they should be reviewed before sharing.
- Includes parser tests and a Windows smoke step. Target path checks on the user's own PC remain unverified until the command is run there.


## Phase 28 — Read-only application cache profiles — implemented on the development branch

- Run **python -m bot.main --pc-app-cleanup-preview** or choose **X** in BOTPYTON.bat.
- Adds a separate application-cache catalog for recognized Google Chrome, Microsoft Edge, Brave and Firefox profile layouts. It looks only inside fixed cache locations (Cache/Cache_Data, Code Cache, GPUCache, and Firefox cache2) under recognized Default / Profile N / Firefox profile directories.
- Reports exact file paths, browser, profile, cache area, size and age for cache files at least one day old by default. This is a **preview only**: no file contents are read, moved or deleted; no settings change; no network/API use.
- Cookies, history, saved passwords, autofill, sessions, downloads, bookmarks, extensions, browser databases and arbitrary application folders are not eligible. Guest/unknown Chromium profile names are not traversed.
- Bounds: 3,000 entries, 200 candidate files, 100 profile-directory entries and five seconds by default; hard caps are 5,000 entries / 300 candidates / 10 seconds. Linked/reparse roots and files are skipped; access failures, unsafe paths, timeouts and budget limits are reported as partial rather than exhaustive.
- Automated tests cover supported browser paths, privacy-sensitive exclusions, unsupported platforms and scan/candidate caps. Windows CI smoke invokes the command on the candidate SHA.
- This phase expands the **preview/catalog** gap only. It does not yet perform browser-cache cleanup, measure boot/performance impact, resolve publisher/signature metadata, or prove that every cache file is safe to remove. Do not infer reclaimable bytes from cache size alone.
- Local paths may contain user-specific folder names. Inspect output before sharing. The report never authorizes an automatic or background cleanup.


## Phase 29 — Explicit browser-cache quarantine and verified restore

- Commands: \`python -m bot.main --pc-app-cache-quarantine\`, \`--pc-app-cache-restore-preview MANIFEST_JSON\`, and \`--pc-app-cache-restore MANIFEST_JSON\`. Windows launcher: **Y** to select/quarantine and **Z** to preview/restore.
- Quarantine enumerates only a **complete fresh preview** of allowlisted Chrome, Edge, Brave and Firefox cache locations. The user selects displayed item numbers. Selection is capped at 25 files; per-file size is capped at 32 MiB and the selected batch at 128 MiB.
- Before the first move it checks exact path membership, file identity/size/nanosecond mtime, regular-file status, link/reparse components and SHA-256. Files move into a separate per-user \`%LOCALAPPDATA%\\BOTPYTON\\BrowserCacheQuarantine\` session with a checksummed manifest. Files are moved, not permanently deleted; this command does not clear cookies or browser databases.
- Exact confirmations are required: \`QUARANTINE SELECTED BROWSER CACHE\` and \`RESTORE BOTPYTON BROWSER CACHE\`. Typing anything else performs no move/restore.
- Restore preview validates the manifest checksum, known original cache roots, stored file SHA-256 and destination conflicts. Restore refuses existing destinations and uses an atomic no-overwrite file creation; conflicts remain in quarantine. Parents must still exist under recognized cache roots.
- All actions are local and sequential. No background service, network call, AI API, registry edit or browser launch is performed. Locked files, changed files, incomplete scans, hash/size cap hits and I/O errors fail closed or produce explicit partial reports.
- Tests use temporary synthetic profiles to verify selected-only movement, integrity checks, wrong confirmation, outside-root path rejection, changed-file rejection, restore, manifest tampering and no-overwrite conflicts. Windows CI runs the focused fixture suite. A CI pass is not target-PC validation.
- Limits: this is still a deliberately narrow cache-cleaning feature, not full CCleaner parity. Close browsers first. Manifests contain local file paths; inspect them before sharing. SHA-256 detects changes but is not a digital signature. Atomic moves can be rejected if the filesystem/volume does not support them.


## Phase 30 — Expanded desktop application cache catalog (preview only)

- Adds `python -m bot.main --pc-desktop-cache-preview` for explicitly allowlisted Discord and Slack Electron cache folders under the current user's `%APPDATA%`; this is a separate catalog from the browser-cache quarantine command.
- Eligible locations are only `Cache`, `Code Cache`, and `GPUCache` under each recognized app root. Files at least one day old are listed with app, cache area, exact path, size and age.
- Excludes app databases, Local Storage, IndexedDB, cookies, tokens, sessions, settings, attachments, downloads/media, and any other app/root not explicitly named. The code does not read file contents, move/delete files, change settings, contact the network, or call AI APIs.
- Bounds: default 1,000 entries, 100 candidates, depth 3 and five seconds; hard caps 2,000 entries, 150 candidates, depth 3 and eight seconds. Links/reparse points are skipped; errors and limits mark the result partial.
- Adds fixture tests and a Windows CI smoke invocation. This phase broadens **discovery only**. Discord/Slack candidates are deliberately not accepted by the existing browser quarantine/restore workflow; a future movement feature must separately review each app's lifecycle, path safety and restore behavior first.
- Old age does not prove a file is inactive or safe to remove. Close the relevant app before any future cleanup. No target-PC files have been changed; target machine validation remains pending.


## Phase 31 — Desktop-cache profile path hardening (preview only)

- Refuses relative profile roots and, on Windows, rejects UNC paths and any profile drive that the OS does not report as a fixed local drive. If drive classification fails, the scanner fails closed rather than touching a possible network share.
- Checks the configured `%APPDATA%` path and every ancestor for symbolic links/reparse points before exploring Discord or Slack; the application and cache directory entries continue to be checked individually.
- Adds a fixture test for a redirected/symlinked ancestor. The test confirms the report becomes partial, no candidate is returned, and the original cache file remains untouched.
- No new app types are enabled and no quarantine/delete action is added. The command remains an inventory-only, bounded, read-only operation.
