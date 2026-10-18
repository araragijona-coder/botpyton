# Phase 43 — Unique and failure-cleaned executor report staging

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `a6a047865e42ce4d9c9d556e53095c1f30e57fe5`
- Component: `bot/executor.py`, `TaskExecutor._write_report()`

## Defect

Report writes used a predictable temporary filename derived from the final report path: `<task-hash>.json.tmp`. If two executions for the same task ID overlap (for example, an external caller starts duplicate executions or a retry is launched before a previous writer has fully exited), both can open/truncate the same staging file. A replace can then race with another open writer, particularly on Windows. Additionally, any serialization/flush/replace exception could leave the staging file behind because cleanup was not in a `finally` block.

The final report itself was only replaced after the write, which is a useful boundary; the issue is that staging was not isolated per writer and failure cleanup was missing.

## Correction

- Create each staging file with `tempfile.mkstemp` in the final report directory, giving every writer a unique, exclusively created path.
- Keep staging on the same filesystem/volume as the final report, preserving atomic replacement semantics.
- Keep flush and `fsync` before `os.replace`.
- Attempt to unlink the staging file in `finally`; ignore cleanup errors so they do not mask the original publication failure.
- Do not change task scheduling, action permissions, final report naming, or task execution policy.

## Regression coverage

- Write twice to a report for the same task ID and capture the `os.replace` sources; each publication must use a different staging path while the final report contains complete JSON.
- Inject a JSON serialization failure after staging begins; assert no temporary staging file remains and the previous final report remains byte-for-byte unchanged.

## Official maintenance research

These are analogous reliability/security lessons, not claims of the same BOTPYTON bug:

- [Python `tempfile.mkstemp` documentation](https://docs.python.org/3/library/tempfile.html) explains that exclusive creation avoids name races under supported OS semantics and documents the caller's responsibility to close descriptors and remove temporary files.
- [CCleaner official Version History](https://www.ccleaner.com/ccleaner/version-history/) says v7.12.1526 (29 September 2026) fixed a file-write security issue and a timing issue during shutdown that could crash while background tasks were still running.
- [BleachBit releases on GitHub](https://github.com/bleachbit/bleachbit/releases) lists v6.0.2 as a maintenance release fixing a Windows security issue that could let malicious actors delete arbitrary files during elevated-privilege cleaning; its notes also mention general bug fixes and performance improvements. This demonstrates why file-operation boundaries and per-run isolation need deliberate handling.

## Validation gate

Run Linux foundation tests and Windows low-memory smoke on the PR candidate. Merge only if both pass, then confirm both workflows pass on the final `main` SHA. These are test-fixture checks, not validation on the user's actual Windows PC.
