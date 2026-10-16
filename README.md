# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with a strict allowlist of implemented operations. Task text is metadata, never a command.

## Capabilities

- Persistent local JSON queue with lifecycle states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Repository inventory, Python syntax inspection and a fixed unit-test invocation using Python's standard library.
- Conservative diagnostics for missing modules, Python syntax errors, invalid JSON and Git conflict markers.
- Machine-readable execution reports and an integrity-checked queue checkpoint that can travel between GitHub Actions runs as an artifact.
- No automatic source edits, package installation, branch writes, pull-request creation or merge.

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

The worker implements the predefined actions `inspect_repository`, `validate_task`, `run_tests`, `apply_known_fix` (non-destructive diagnosis) and `prepare_report`. The unit-test action always runs the fixed command `python -m unittest discover -s tests -v`, with `shell=False` and a timeout.

## Cross-run queue persistence in GitHub Actions

The dedicated workflow is `.github/workflows/botpyton-worker.yml`. It uses the GitHub Actions artifact store to carry the queue from one execution to the next, on the **same branch**.

- On the phase branch `bot-bootstrap/phase-04-persistent-checkpoint`, a push runs the worker and creates a first smoke-task checkpoint if none exists. Later runs search the same workflow's completed runs on that branch and restore the latest non-expired `botpyton-queue-checkpoint` artifact.
- After the workflow is merged into the repository's default branch, it can be started manually from the Actions tab on a selected branch. The `tasks_json` input accepts one task object or an array; `work_limit` bounds claims to 1–100. Task action validation and queue dependency validation still apply.
- Before using a restored queue, BotPyton verifies the checkpoint manifest, SHA-256 digest, task fingerprints, lifecycle states, attempt limits and dependency graph. A corrupt or incomplete checkpoint fails closed instead of replacing memory.
- If the previous worker was interrupted with tasks marked `running`, the next execution runs `--resume` and respects each task's attempt limit.
- The worker token is limited to `actions: read` and `contents: read`. The workflow does not commit state to a branch or use AI APIs. Artifact upload uses GitHub's workflow artifact mechanism.

Manual task input example (JSON; no command strings are executed):

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

For a local/manual checkpoint round trip, use:

```bash
python -m bot.main --export-checkpoint .cerebro/checkpoint-out --memory .cerebro/memory/state.json
python -m bot.main --restore-checkpoint .cerebro/checkpoint-out --memory .cerebro/memory/restored-state.json
```

### Persistence limit

GitHub Actions artifacts have a finite retention period. This workflow requests **90 days**; repository or organization policy may enforce a shorter maximum. If no retained checkpoint remains, the workflow initializes an empty queue (and seeds a smoke task only on the first push to the phase branch). Artifact-based persistence is suitable for continuity between runs, but it is not indefinite archival storage. Runs are serialized per branch to avoid checkpoint races. Work must be designed to tolerate at-least-once execution if a runner disappears before it uploads the latest checkpoint.

## Recovery behavior

- `ModuleNotFoundError`: inspect the missing import and project dependency declarations; do not install packages automatically.
- `SyntaxError`: report file/line syntax findings; do not rewrite code generically.
- `JSONDecodeError`: parse repository JSON files and report invalid paths and locations.
- `CONFLICT (content)`: search for common conflict markers; do not merge or rewrite files.
- Unknown signatures: stop and report without retry.

This phase performs **no automatic source repair**: plausible edits are not proof of correctness.

## Security model

- Python standard library only; no AI/LLM services or inference API calls.
- Actions are allowlisted in source code; no arbitrary shell commands from task descriptions.
- Fixed test command, timeout, and bounded task attempts.
- Minimal workflow permissions: `actions: read`, `contents: read`.
- No access to repository secrets, automatic source edits, branch commits, pull requests or merges.

## Current status

**PHASE 04 — CROSS-RUN ARTIFACT CHECKPOINT / NOT YET PRODUCTION-READY.** The artifact checkpoint codec, integrity checks, restore command, dedicated worker workflow and queue round-trip CI tests are implemented. This does not enable indefinite retention or autonomous code changes.
