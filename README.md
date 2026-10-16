# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with a strict allowlist of implemented operations. Task text is metadata, never a command.

## Capabilities in this phase

- Persistent local JSON queue with lifecycle states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Repository inventory and Python syntax inspection.
- Fixed unit-test invocation using Python's standard library; no `shell=True` and no command from task data is executed.
- Known-error classification for missing modules, Python syntax errors, invalid JSON and Git conflict markers.
- Machine-readable JSON execution reports, which CI uploads as a short-lived artifact.
- No automatic edits to source files, package installation, branch creation or merge. Known-error handling gathers evidence and selects a conservative recovery path; unknown failures stop.

## Quick start

Requires Python 3.11+.

```bash
python -m bot.main --check
python -m unittest discover -s tests -v
python -m bot.main --validate-task .cerebro/examples/example-task.json
python -m bot.main --plan .cerebro/examples/example-plan.json
```

## Queue and worker

State defaults to `.cerebro/memory/state.json`; pass `--memory PATH` to choose another file.

```bash
python -m bot.main --enqueue .cerebro/examples/worker-task.json
python -m bot.main --queue
python -m bot.main --work-once
python -m bot.main --work-queue 10
python -m bot.main --resume
```

The worker implements these predefined actions: `inspect_repository`, `validate_task`, `run_tests`, `apply_known_fix` (evidence-based, non-destructive recovery), and `prepare_report`. It executes them only when explicitly included in the task's allowlisted `allowed_actions` array. The test action always runs the fixed command `python -m unittest discover -s tests -v`.

After each attempted task, a report is written to `.cerebro/reports/` using a hash of the task ID as the filename. A failed task is retried only when its known-error rule says retryable and attempts remain. Unknown failures are recorded and stop; the executor does not rewrite source heuristically.

To classify a local error log, save it in a repository-relative file and run:

```bash
python -m bot.main --diagnose .cerebro/reports/test-error.log
```

## Recovery behavior

- `ModuleNotFoundError`: inspects the missing import and project dependency declarations; it does not install packages.
- `SyntaxError`: runs a syntax inspection and reports discovered file/line errors; it does not rewrite code.
- `JSONDecodeError`: parses repository JSON files and reports invalid paths and locations.
- `CONFLICT (content)`: searches for common merge-conflict markers; it never merges or rewrites files.
- Unknown signatures: stop and report with no retry.

This is intentionally conservative. General-purpose automatic code repair is not enabled because a syntactically plausible edit is not proof of correctness.

## Important persistence boundary

The JSON queue survives process restarts **if the same file is retained**. A fresh GitHub-hosted Actions runner has an ephemeral filesystem. The CI workflow therefore uses a runner-temporary queue file and uploads execution reports as a short-lived artifact; it does not yet recover the queue automatically across separate workflow runs. That is a separate storage phase.

## Safety model

- Python standard library only; no AI/LLM services or inference API calls.
- Actions are allowlisted in source code; no arbitrary shell commands from task descriptions.
- Test command uses a fixed argument vector with `shell=False` and a timeout.
- Workflow permissions remain `contents: read`.
- No automatic source edits, secret access, branch writes, pull-request creation or merge.

## Current status

**PHASE 03 — DETERMINISTIC EXECUTOR / NOT YET PRODUCTION-READY.** The executor, diagnostics, bounded queue retry path, integration task and CI report artifact are implemented. This phase does not yet perform autonomous code changes or cross-run queue persistence.
