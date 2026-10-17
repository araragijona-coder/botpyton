# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with strict allowlists. Task descriptions are data, never commands.

## Capabilities

- JSON task queue with explicit states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Cross-run queue checkpoints in GitHub Actions artifacts, with SHA-256 integrity checks.
- Repository inventory, syntax checks, fixed unit-test execution and JSON execution reports.
- Bounded autonomous cycle: run fixed tests, apply at most one exact known repair after a failure, rerun tests, verify Python/JSON files, and persist per-attempt evidence.
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

## Phase 07: bounded autonomous repair cycle

Run this from the repository root:

```bash
python -m bot.main --autonomous-cycle
python -m bot.main --autonomous-cycle --cycle-max-attempts 2 --cycle-timeout 60
```

The cycle runs only the fixed command `python -m unittest discover -s tests -v` with `shell=False`. Each invocation is bounded to 1–3 test attempts and 1–120 seconds per attempt. After a failed test run, it may use the existing exact-pattern repair rules in `bot/fixes.py` once; the original file is backed up and the candidate must pass parser verification. After repairs, the full Python syntax and JSON-file checks must pass before the test suite is rerun. The final successful state requires both tests and static verification to pass.

An unsupported/ambiguous failure, timeout, failed post-repair verification, or exhausted limit stops the cycle. It does not retry blindly and does not make an unverified repair after the final permitted test attempt. Every attempt has a separate JSON record under `.cerebro/reports/autonomous-cycles/<cycle-id>/attempt-NNN.json`; `summary.json` is updated atomically and includes the outcome, attempt list, and explicit no-AI flags. Records are persisted before the test starts and updated before/after repair work so interrupted cycles leave evidence. The normal CI invokes the cycle and validates its successful audit record; reports are uploaded as a CI artifact.

The test subprocess receives a filtered environment that removes common credential-bearing variable names. The cycle makes no network/API calls, does not install dependencies, does not merge branches, and never executes commands supplied in JSON or task text.

## Phase 08: JSON task orchestration from GitHub

The persistent worker workflow at .github/workflows/botpyton-worker.yml accepts a bounded JSON task batch and a separate batch of GitHub change plans from workflow_dispatch. These inputs are JSON data; task descriptions are never interpreted as shell commands.

### Validate task batches locally

~~~bash
python -m bot.main --validate-task-batch .cerebro/examples/github-task-batch.json
python -m bot.main --enqueue .cerebro/examples/github-task-batch.json --memory .cerebro/memory/state.json
python -m bot.main --work-queue 10 --memory .cerebro/memory/state.json
~~~

Task batch validation checks each task against the fixed action allowlist, rejects duplicate IDs and internal dependency cycles, and imposes a maximum of 25 tasks and 50,000 input bytes. References to earlier persisted tasks are allowed at batch-validation time; TaskQueue.enqueue_many then checks dependency existence and cycles atomically against the complete queue before persisting any incoming task. Work per dispatch is limited to 1–100 queue claims, and each task's max_attempts is separately limited to 1–3.

The worker restores the most recent retained botpyton-queue-checkpoint from a previous completed run of the same workflow and branch, validates its SHA-256 manifest, resumes interrupted work, and uploads the next checkpoint. GitHub Actions artifacts are branch-scoped in this workflow and have a configured/requested 90-day retention period; they are not indefinite archival storage.

### Draft pull requests from a declarative plan batch

Validate a batch without making GitHub API calls (the repository identity must be configured for the dry-run summary):

~~~bash
GITHUB_REPOSITORY=owner/repo python -m bot.main --github-plans .cerebro/examples/github-change-plan-batch.json
~~~

Real branch/commit/PR operations require an explicit opt-in and a suitable repository-scoped GITHUB_TOKEN:

~~~bash
python -m bot.main --github-plans .cerebro/examples/github-change-plan-batch.json --execute-github-plans
~~~

Plan validation happens for the entire batch before the first GitHub write. A batch may contain up to 10 plans and the input is limited to 50,000 bytes. Every plan must set draft: true; path, extension, size and protected-location rules use the Phase 06 policy. The submit loop is sequential, branch names are deterministic, and existing mismatched changes are rejected. If a later GitHub API call fails after earlier plans were submitted, those earlier draft PRs remain open for human review; there is no rollback or merge operation.

### GitHub Actions dispatch

Use the BotPyton persistent worker workflow and select the branch containing this phase. Supply tasks_json as one task object or an array, and change_plans_json as an array. Both are validated before queue access. work_limit must be 1–100. execute_prs defaults to false; when false, change plans are validated and summarized in dry-run mode only. Set it to true to enable the separate submit job.

The write job has only contents: write and pull-requests: write, and runs only for an explicit manual dispatch with execute_prs=true, after the validation and worker jobs succeed. The normal queue job has read-only repository permissions plus actions-read for checkpoints. There is no automatic merge, no AI/LLM service, and no AI API request. The workflow must be present on the repository's default branch for GitHub to expose workflow_dispatch; this development branch has not been merged automatically.

Examples: .cerebro/examples/github-task-batch.json and .cerebro/examples/github-change-plan-batch.json.

## Phase 09 / I-01: privileged PR sender isolation (fail-closed)

The two privileged submission jobs in `.github/workflows/botpyton-worker.yml` and `.github/workflows/botpyton-github-ops.yml` are deliberately disabled with `if: false`. Their former design checked out the selected workflow ref and executed repository Python code while exposing a write-capable token. A condition inside a workflow is not, by itself, an enforceable trust boundary when a caller can select a ref whose workflow definition may be changed.

Live PR submission remains disabled until repository administrators verify an independent trusted-code boundary and effective repository controls (branch protection/rulesets and, where used, required environment reviewers). Those controls were not established by this code change. Do not enable either job by changing its condition alone. No live submission was performed as part of I-01.

The common `validate_change_plan()` validator now rejects `draft: false`, so individual and batch paths share the draft-only policy. The batch validator continues to validate the complete batch before any write operation.

## Security model

- Python standard library only; no AI/LLM services or inference APIs.
- GitHub REST operations are restricted to the fixed `api.github.com` host and the configured repository.
- No arbitrary shell command from task text.
- Source-change plans are path-validated, size-limited, fingerprinted and committed on isolated branches.
- Each proposed PR is a draft by default and requires human review.
- No automatic merge, no force-push, no secret administration, and no write permissions in the normal test workflow.

## Current status

**PHASE 08 — GITHUB JSON TASK ORCHESTRATION / VERIFIED ON DEVELOPMENT BRANCH.** The phase branch passed the foundation test workflow (71 unit tests plus the autonomous-cycle and audit-record checks) and the persistent worker workflow (checkpoint restored, queue work completed, next checkpoint uploaded). The change remains isolated from main; live draft-PR submission was not executed. Any promotion still requires human review.


## Phase 11: on-demand local use and bounded repository inventory

BOTPYTON is intended to run **only when the user opens it**. The optional Windows launcher `BOTPYTON.bat` does not install a service, register a startup task, or keep a background process alive after the window closes. It offers a read-only project inventory, a core check, and an explicit choice to run the test suite. Python must already be installed and available as `python` on PATH.

Inspect a project without executing its code:

```bash
python -m bot.main --inspect-repository "C:\\path\\to\\project"
```

The inventory has bounds for file count, directory depth, and oversized files; skips common generated/dependency folders and symlinks; recognizes common language extensions and project manifests; and reports possible checks as suggestions only. It does not read source-file contents, install dependencies, run candidate commands, use the network, or call AI services. The default limits are 5,000 files, depth 8, and 2 MB per file for oversized-file reporting. An inventory marked `inventory_complete_with_limits` is partial and must not be treated as exhaustive.

The inventory is an initial capability, not a complete local-PC agent: it does not yet edit arbitrary projects or launch arbitrary tools. Any future execution/editing capability must be separately permissioned and bounded. The educational project catalog is in `docs/EDUCATIONAL-PROJECT-LIBRARY.json`; entries marked planned are ideas, not implemented applications.


## Tool discovery and local changes (Phase 11)

List developer tools found on the current PATH without executing them:

```bash
python -m bot.main --tools
python -m bot.main --probe-tool git
```

Only the second command runs a fixed, source-registered version probe. It does not accept arbitrary command arguments.

Local file-change plans are dry-run by default:

```bash
python -m bot.main --local-plan "C:\\path\\plan.json" --project-root "C:\\path\\project"
```

To apply a plan, add `--execute-local-plan`. Existing files require a matching `expected_sha256`; changes are limited to 20 files, 200 KB per file and 500 KB of total content. Existing files are backed up outside the project root. Protected paths, traversal and symlink path components are rejected. See `docs/LOCAL-CHANGE-PLANS.md` for the schema and protections.

The process runner uses separate arguments, an explicit executable allowlist, `shell=False`, a timeout, disconnected stdin, stripped credential-like environment variables and bounded returned output. It is not a general shell and does not execute commands discovered in repository text. These controls are foundations for the worker, not a claim that free-form programming tasks can already be autonomously solved.


Local change plans can also be submitted as queue tasks using the allowlisted action `apply_local_plan`; the plan `task_id` must match the queue task `id`. This routes structured edits through the same deterministic worker and report pipeline instead of bypassing it. The goal text is treated as a label, not as executable instructions. Without an AI model, free-form requests still need an explicit plan or a deterministic procedure supported by the worker.


### Queue a structured local programming task

The Windows launcher now exposes queue actions: add a JSON task, inspect the queue, and execute one task at a time. The included example `.cerebro/examples/worker-local-change-task.json` creates `botpyton_demo_output/hello.py` only after the user explicitly runs the queued task.

CLI equivalent (run from the BOTPYTON repository root):

```bash
python -m bot.main --enqueue .cerebro/examples/worker-local-change-task.json
python -m bot.main --queue
python -m bot.main --work-once
```

The queue stores durable state under `.cerebro/memory/state.json` by default. Tasks are JSON and actions are allowlisted. This is an auditable, deterministic work order, not natural-language AI reasoning. Review any task JSON before queueing it, especially tasks that contain `apply_local_plan`.


## Resource-aware operation

`python -m bot.main --system-profile` reports the current operating system, Python executable, CPU count, available physical RAM and free disk space using lightweight standard-library/system calls. The process runner chooses a conservative child memory budget from available RAM: at most 768 MB and generally no more than half of currently available memory. On Windows, the runner applies that budget through a Job Object and terminates child processes when the job closes. On other platforms the memory limit is reported as not enforced; timeout, serial execution and bounded output capture still apply.

Because the reported available RAM varies with what Windows and other applications are using, BOTPYTON calculates the budget at runtime rather than assuming the full 16 GB is available.
