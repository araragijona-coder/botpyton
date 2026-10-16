# BotPyton — autonomous GitHub worker (no AI)

BotPyton is a deterministic software-work automation bot. It uses **no AI models, no inference APIs, and no AI service credentials**. This phase adds a persistent JSON task queue with dependency-aware scheduling, explicit lifecycle states, restart recovery, bounded retries and idempotent enqueue/completion.

## Initial scope

- Python standard library for the core engine.
- GitHub Actions for event-driven validation.
- JSON for configuration, task records and durable local queue state.
- Explicit action allowlists and bounded retries (1–3 attempts).
- No automatic merge to `main`, no secret administration, and no arbitrary shell commands from task text.

## Quick start

Requires Python 3.11+.

```bash
python -m bot.main --check
python -m unittest discover -s tests -v
python -m bot.main --validate-task .cerebro/examples/example-task.json
python -m bot.main --plan .cerebro/examples/example-plan.json
```

## Queue commands

Queue state defaults to `.cerebro/memory/state.json`; override it with `--memory PATH`.

```bash
# Add one task or a JSON array. Repeating the same ID + payload is a no-op.
python -m bot.main --enqueue .cerebro/examples/example-task.json

# Inspect state; claim the next dependency-ready task.
python -m bot.main --queue
python -m bot.main --claim-next

# Mark the claimed task complete, or fail it with an explicit reason.
python -m bot.main --complete TASK-EXAMPLE-001 --result '{"ok":true}'
python -m bot.main --fail TASK-EXAMPLE-001 --error "known transient failure" --retryable

# Requeue tasks left running by an interrupted process, within attempt limits.
python -m bot.main --resume
```

Task IDs are idempotency keys for their canonical JSON payload. Re-enqueuing the same ID with changed content fails closed. Dependencies must exist in the queue or incoming batch and cycles are rejected. A task is claimable only when every dependency succeeded. A failed dependency blocks its descendants. Completion can be repeated only with the same result.

## Important persistence boundary

The JSON store survives process restarts **as long as the same file is retained**. A fresh GitHub-hosted Actions runner has an ephemeral filesystem: cross-run durability still requires a deliberately configured GitHub artifact or repository-backed checkpoint workflow. This phase does not silently grant write permissions or commit mutable state to the source branch.

## Repository layout

- `bot/`: deterministic planner, policy, JSON memory, queue, recovery and verification.
- `.cerebro/`: configuration, examples and memory guidance.
- `.github/workflows/`: CI and manual validation.
- `tests/`: standard-library unit tests.

## Safety model

Task descriptions are data, never shell commands. A repair rule may select only a strategy implemented in code. Unknown errors stop and produce a report. The bot never merges pull requests automatically. Workflow write permissions must be reviewed before enabling write operations.

## Current status

**PHASE 02 — PERSISTENT LOCAL QUEUE / NOT YET PRODUCTION-READY.** Queue lifecycle, dependency checks, restart recovery and idempotency are implemented and tested. This is not yet an autonomous code-changing worker, and runner-to-run persistence has not been enabled.
