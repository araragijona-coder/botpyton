# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with strict allowlists. Task descriptions are data, never commands.

## Capabilities

- JSON task queue with explicit states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Cross-run queue checkpoints in GitHub Actions artifacts, with SHA-256 integrity checks.
- Repository inventory, syntax checks, fixed unit-test execution and JSON execution reports.
- Conservative diagnostics plus two exact source repairs: a uniquely located JSON trailing comma and a supported one-line Python compound-header colon.
- Declarative GitHub operations: create an isolated bot branch, create one commit containing the declared paths, and open a draft pull request for review.
- No automatic merge, no arbitrary shell commands, no automatic package installation, and no AI/API calls to AI services.

## Quick start

Requires Python 3.11+.

```bash
python -m bot.main --check
python -m unittest discover -s tests -v
python -m bot.main --validate-task .cerebro/examples/example-task.json
python -m bot.main --plan .cerebro/examples/example-plan.json
```

## Queue and worker

Queue state defaults to `.cerebro/memory/state.json`; pass `--memory PATH` to choose another file.

```bash
python -m bot.main --enqueue .cerebro/examples/worker-task.json
python -m bot.main --queue
python -m bot.main --work-once
python -m bot.main --work-queue 10
python -m bot.main --resume
```

The worker supports the fixed actions `inspect_repository`, `validate_task`, `run_tests`, `apply_known_fix` and `prepare_report`. The unit-test action always runs `python -m unittest discover -s tests -v`, with `shell=False` and a timeout.

## Phase 05: known fixes

Rules in `bot/fixes.py` remove a single trailing comma from one invalid JSON file, or add a missing colon only when Python reports `SyntaxError: expected ':'` on a supported one-line `if`, `elif`, `for`, `while`, `def` or `class` header. Both rules require one uniquely identified broken file, parser verification and a timestamped copy under `.cerebro/backups/`. Unknown or ambiguous failures are never automatically edited. `.github/`, configuration, policy, memory, report and backup paths are protected.

## Phase 06: branch, commit and draft pull request

GitHub operations live in `bot/github_ops.py`. The JSON change plan is validated before any GitHub request. It has a strict path allowlist (`.py`, `.md`, `.json`, `.toml`, `.txt`), rejects duplicate/traversal/protected paths, and is bounded to 20 files, 200 KB per file and 500 KB total.

Local or CI dry-run (no API requests, no token required; set repository identity):

```bash
GITHUB_REPOSITORY=owner/repo python -m bot.main --github-plan .cerebro/examples/github-change-plan.json
```

To execute real GitHub operations, the caller must explicitly supply `--execute-github-plan` and a repository-scoped `GITHUB_TOKEN`:

```bash
python -m bot.main --github-plan .cerebro/examples/github-change-plan.json --execute-github-plan
```

Actual submission performs these steps:

1. Confirm the plan targets the repository's default branch.
2. Generate a deterministic `botpyton/<task>-<fingerprint>` branch name and create the branch from the current default-branch tip if absent.
3. Create a Git tree and one commit containing only the declared allowed files, then update that branch without force-pushing.
4. Open a draft pull request; if the same branch/plan already has an open PR, return it instead of creating a duplicate.

Repeated submissions of the same plan are idempotent. If a pre-existing deterministic branch has content that does not match the proposed plan, the operation stops rather than overwriting it. A closed prior PR is not duplicated. The engine contains no merge function.

### Manual workflow

The dedicated workflow is `.github/workflows/botpyton-github-ops.yml`. It is triggered by pushes to the phase branch for read-only CI and has a separate manual-submit job. The job that can write to GitHub runs **only** for an explicit `workflow_dispatch` with `execute=true`.

- Default mode: validate and print a dry-run summary.
- Execute mode: create a bot branch, one commit and a draft PR.
- Read-only validation job: `contents: read`.
- Actual submission job: `contents: write` and `pull-requests: write`, only; no actions-write permission.
- The workflow runs with `GITHUB_TOKEN` provided by Actions. No PAT or AI-service secret is needed.
- The workflow must first exist on the repository's default branch before GitHub exposes the manual dispatch control. This phase branch has not been merged automatically.

Example plan: `.cerebro/examples/github-change-plan.json`. It only proposes adding a documentation example; actual submission is disabled by default.

## Phase 04: cross-run queue persistence

The dedicated worker `.github/workflows/botpyton-worker.yml` uses GitHub Actions artifacts to carry queue state between runs of the same branch. It verifies the manifest, SHA-256 digest, task fingerprints, status values, retry bounds and dependency graph before restore. If a prior runner disappears with tasks marked `running`, the next run attempts bounded recovery.

Checkpoint retention is configured to request up to 90 days, subject to GitHub policies; it is not indefinite archival storage.

## Security model

- Python standard library only; no AI/LLM services or inference APIs.
- GitHub REST operations are restricted to the fixed `api.github.com` host and the configured repository.
- No arbitrary shell command from task text.
- Source-change plans are path-validated, size-limited, fingerprinted and committed on isolated branches.
- Each proposed PR is a draft by default and requires human review.
- No automatic merge, no force-push, no secret administration, and no write permissions in the normal test workflow.

## Current status

**PHASE 06 — GITHUB BRANCH/COMMIT/DRAFT-PR OPERATIONS / NOT YET PRODUCTION-READY.** The implementation and fake-transport tests are in place. The normal CI path must pass before the phase is verified. The actual write workflow is gated behind a manual explicit opt-in and has not been used to create a live PR.
