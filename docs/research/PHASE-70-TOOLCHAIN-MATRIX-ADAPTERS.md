# Phase 70 — Deterministic toolchain matrix and adapters

## Status and baseline

- Status: implementation candidate; isolated branch only.
- Parent branch: `bot-bootstrap/phase-69-github-publication-reconciliation`
- Parent SHA: `35752e007c4eeb7490f22a7d8fb9612a64344847`
- Candidate branch: `bot-bootstrap/phase-70-toolchain-matrix-adapters`
- Scope: inventory and deterministic recommendations only; no project build/test runs from the matrix command.

## User-facing command

```powershell
python -m bot.main --toolchain-matrix --project-root "C:\projects\my-app"
```

The command emits a bounded JSON matrix of detected project ecosystems, marker paths, registered tool IDs available on PATH, required tool groups, optional tools, and test profiles discovered at the selected project root. It never executes a tool version probe, project script, build, test, GUI application, package installation or network request.

Existing project checks remain separately discoverable:

```powershell
python -m bot.main --list-project-checks --project-root "C:\projects\my-app"
```

Execution of an individual discovered check remains an independent operation and requires explicit approval after its command and project code have been reviewed:

```powershell
python -m bot.main --run-project-check gradle-test --project-root "C:\projects\my-app" --execute-project-code
```

A discovered manifest or script name never constitutes authorization to execute it. Build/test commands from projects can have side effects and may access the network; the existing process runner does not enforce network isolation.

## Adapter inventory

The static matrix recognizes signals for Python, JavaScript/TypeScript, .NET, Java/Kotlin, Android, Rust, Go, C/C++, PHP, Ruby, PowerShell, Godot and Blender projects. It uses registered tool IDs and only reports their availability through PATH resolution. It does not search every vendor installation directory. A tool that is installed but not on PATH may be reported as unavailable.

For each adapter:

- `manifest_or_project_marker` means a manifest or known project marker was detected.
- `source_or_artifact_extension_only` means detection rests on file extensions, which is weaker evidence.
- `required_tool_missing` means one or more registered required tool groups could not be satisfied.
- `known_profile_available` means a matching fixed project-check profile was discovered at the selected root.
- `detected_no_matching_builtin_profile` means the project signal was found, but the current profile inventory did not produce a matching executable check.

The matrix is intentionally not a package manager and not a universal build executor. For graphical engines, identifying the project and editor executable does not validate scenes, gameplay, rendering, input or performance. Godot/Blender are inventory-only in this phase; execution profiles require a separately reviewed, narrowly scoped follow-up.

The recursive scan is bounded to 2,000 entries and depth five, and skips generated, dependency and common IDE folders, symlinks and junctions when detectable. On platforms that support handle-relative, no-follow directory opens, it walks path components through directory handles and checks each directory identity before enumeration. The Windows fallback checks identity before and after path-based enumeration and discards that directory's entries if the path changes; this is race hardening, not a kernel-level filesystem sandbox. If the scan hits a limit or an unreadable directory, the report becomes `partial`; missing results must not be interpreted as proof that a technology is absent. Test-profile discovery is root-only. Nested projects are inventory evidence, not execution authorization.

## Safety and invariants

- Standard-library implementation; no new third-party runtime dependency.
- No model, external AI or AI API integration.
- No network request, version probe or project execution.
- No file creation, editing, deletion or installation.
- Tool and project-check inventories are reported separately from authorization.
- Existing tool probes and project-check commands remain explicitly invoked operations.
- JSON reports do not include tool executable paths or manifest contents.
- Unknown technologies remain unclassified rather than guessed.

## Automated regression coverage

The Phase 70 suite tests multiple project markers, available and missing tools, graphics-project detection without launching a GUI, skipped generated folders, symlink refusal and a simulated directory-replacement race, deterministic bounded scanning, depth and unreadable-directory reporting, nested Python/Android profile association, Markdown fence integrity, invalid roots, and the standalone CLI JSON contract.

Acceptance gate: all existing and new unit tests, syntax validation, portable package verification, and both Linux foundation CI and Windows low-memory CI must pass on the same final candidate SHA. Passing CI is evidence for the covered tests, not proof of all toolchains being installed or usable on the user's PC.

## Stop conditions and known limits

Stop or return a partial/unavailable result if the project root is invalid, the scan is truncated, directories are unreadable, the required tool is unavailable, or no approved check exists. Do not automatically install tools or dependencies, raise privileges, execute scripts, launch a GUI, perform network writes, publish PRs or merge changes.

Phase 70 establishes an inventory and adapter contract. It does not make BotPyton a universal programmer, guarantee successful builds, or complete the experimental recovery loop planned for Phase 71.
