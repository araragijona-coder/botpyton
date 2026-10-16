# BotPyton — autonomous GitHub worker (no AI)

BotPyton is a deterministic software-work automation bot. It uses **no AI models, no inference APIs, and no AI service credentials**. Its initial release is deliberately safe: inspect and validate task plans, persist state, classify known failures, and run local checks. GitHub Actions is the intended execution environment.

## Initial scope

- Python standard library for the core engine.
- GitHub Actions for event-driven execution.
- JSON for configuration and durable task/memory records.
- Explicit allowlists and bounded retries.
- No automatic merge to `main`, no secret administration, and no arbitrary shell commands from task text.

## Quick start

Requires Python 3.11+.

```bash
python -m bot.main --check
python -m unittest discover -s tests -v
```

To validate a task file:

```bash
python -m bot.main --validate-task .cerebro/examples/example-task.json
```

## Repository layout

- `bot/`: deterministic planner, policy, memory, recovery and verification.
- `.cerebro/`: configuration, schemas, examples and durable records.
- `.github/workflows/`: CI and manual validation.
- `tests/`: standard-library tests.

## Safety model

Task descriptions are data, never shell commands. A repair rule may select only a strategy implemented in code. Unknown errors stop and produce a report. The bot never merges pull requests automatically. Workflow write permissions must be reviewed before enabling write operations.

## Current status

**FOUNDATION / NOT YET PRODUCTION-READY.** This branch establishes the architecture and tests. It does not yet create branches, commit source changes, open pull requests, or autonomously modify application code. Those capabilities must be added as separate, tested phases.
