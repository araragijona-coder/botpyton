# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with a strict allowlist of implemented operations. Task text is metadata, never a command.

## Capabilities

- Persistent local JSON queue with lifecycle states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Cross-run queue checkpoints in GitHub Actions artifacts, protected by a SHA-256 manifest and schema validation.
- Repository inventory, Python syntax inspection and a fixed unit-test invocation using Python's standard library.
- Conservative diagnostics for missing modules, Python syntax errors, invalid JSON and Git conflict markers.
- Two exact, deterministic source repairs: remove a single JSON trailing comma, or add a missing colon to a supported one-line Python compound header. Both require parser verification and create a backup first.
- Unknown/ambiguous errors stop without edits. There is no arbitrary code generation, automatic package installation, branch writes, pull-request creation or merge.

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

The worker implements predefined actions: `inspect_repository`, `validate_task`, `run_tests`, `apply_known_fix` and `prepare_report`. The unit-test action always runs the fixed command `python -m unittest discover -s tests -v`, with `shell=False` and a timeout.

## Known fixes (Phase 05)

The exact repair rules live in `bot/fixes.py`; regression tests live in `tests/test_fixes.py`. These rules are intentionally limited:

1. `json.remove_single_trailing_comma`: only considered when the failure log contains `JSONDecodeError`, exactly one repository JSON file is invalid, and the parser's error position points to a closing `}` or `]` preceded by a comma (possibly separated by whitespace). The candidate must parse as complete JSON before it is written.
2. `python.add_missing_compound_header_colon`: only considered when the failure log contains `SyntaxError`, exactly one repository Python file is invalid, Python reports exactly `expected ':'`, and the reported line matches a supported one-line `if`, `elif`, `for`, `while`, `def` or `class` header. The candidate must parse as a complete Python module before it is written.

Before a change, the original file is copied to `.cerebro/backups/` under a unique timestamped name. Every write uses a temporary file and atomic replacement. The following locations are excluded from automatic modification: `.github/`, `.cerebro/config.json`, `.cerebro/policies/`, `.cerebro/memory/`, `.cerebro/reports/` and `.cerebro/backups/`.

A fix is deliberately rejected when there are multiple invalid files, multiple known signatures, unsupported syntax, parser errors after the proposed edit, protected targets or no exact match. It does not guess at missing package names, choose a Git conflict side, rewrite arbitrary code or install dependencies. A task can order `apply_known_fix` before `run_tests`, so a retry applies a supported repair and then verifies the suite.

## Cross-run queue persistence in GitHub Actions

The dedicated workflow is `.github/workflows/botpyton-worker.yml`. It uses GitHub Actions artifacts to carry the queue between executions on the **same branch**.

- It searches completed runs of the worker workflow on the current branch and restores the latest retained `botpyton-queue-checkpoint` artifact.
- Before restoration, BotPyton verifies the manifest, SHA-256 digest, task fingerprints, lifecycle states, attempt limits and dependency graph. Invalid checkpoints fail closed.
- If the previous runner left tasks as `running`, the next run invokes `--resume` within attempt limits.
- The workflow requests up to 90 days of artifact retention, subject to repository/organization limits.
- The worker token has only `actions: read` and `contents: read`; it does not commit state or modify repository branches.

## Example manual task input

Start the workflow manually after the workflow has been merged to the default branch, select a branch containing the workflow, and provide `tasks_json`. The `work_limit` input bounds queue claims from 1 to 100.

```json
[
  {
    "id": "TASK-CHECK-001",
    "goal": "Inspect repository and run unit tests",
    "depends_on": [],
    "allowed_actions": ["inspect_repository", "validate_task", "run_tests", "prepare_report"],
    "success_conditions": ["unit_tests_pass"],
    "max_attempts": 2
  }
]
```

## Recovery behavior

- `ModuleNotFoundError`: inspect imports and declared dependencies; never install packages automatically.
- `SyntaxError`: diagnose all Python syntax errors; automatically fix only the exact supported missing-colon pattern.
- `JSONDecodeError`: diagnose repository JSON; automatically fix only a uniquely located trailing comma.
- `CONFLICT (content)`: locate merge-conflict markers; do not merge or choose a side.
- Unknown signatures: stop, preserve evidence and report.

## Security model

- Python standard library only; no AI/LLM services or inference API calls.
- Actions are allowlisted in source code; no arbitrary shell commands from task descriptions.
- Fixed test command, timeout, and bounded task attempts.
- Automatic writes are limited to the two exact fix rules and exclude protected paths; each repair requires a backup and a successful parser check.
- No secret administration, automatic source-wide rewrites, branch commits, pull requests or merges.

## Current status

**PHASE 05 — EXACT KNOWN FIXES / NOT YET PRODUCTION-READY.** The two deterministic repairs, backups, parser verification and regression tests are implemented. GitHub Actions CI must pass before this phase is considered verified. Cross-run checkpoint persistence remains artifact-based rather than indefinite archival storage.
