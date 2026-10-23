# Architect-order contract and response — Phases 71–72

## Goal

This contract lets an external architecture bot hand a bounded JSON order to BOTPYTON. The external bot may use AI; BOTPYTON itself remains deterministic and offline for this local operation. The packet is untrusted input, not a permission token.

## Commands

Validate and review without queue changes, file edits, tests, or execution:

~~~powershell
python -m bot.main --architect-order .cerebro/examples/architect-order.json
~~~

Execute a validated order only after reviewing its exact task payload and project root:

~~~powershell
python -m bot.main --architect-order .cerebro/examples/architect-order.json --execute-architect-order --project-root "C:\projects\sample"
~~~

If any task includes `apply_local_plan` or `apply_known_fix`, a separate local-write authorization is required:

~~~powershell
python -m bot.main --architect-order order.json --execute-architect-order --authorize-local-writes
~~~

If a task includes `run_tests` or `run_project_check`, pass `--execute-project-code` as a separate explicit approval after reviewing the code to be run. Such project code may access the network; BOTPYTON does not claim to sandbox its networking. Combining approvals when they are not requested is rejected.

## Packet schema version 1

Required fields:

- `schema_version`: integer `1`.
- `order_id`: 1–80 ASCII letters, digits, dot, underscore, or hyphen; first character must be alphanumeric.
- `source`: object with `agent_id` and optional `plan_id`. These are display/correlation labels only; they are not cryptographically authenticated.
- `summary`: non-empty text, maximum 2,000 characters.
- `status`: `ready` or `needs_input`.
- `tasks`: an array using BOTPYTON's existing, source-controlled task schema.

Optional field:

- `questions`: up to 10 non-empty questions, each at most 500 characters.

Unknown fields are rejected, including fields such as `approved`, `execute`, `shell`, or `credentials`. The packet cannot enable execution or grant permissions. A `ready` packet must contain 1–25 valid tasks and no open questions. Each task needs substantive machine-verifiable `verification_checks`; report existence alone is insufficient. A `needs_input` packet must have zero tasks and at least one question; it is never enqueued or run.

## Authorization and safety model

- Dry-run is the default. It reports task IDs, dependency order, required local approvals, verification requirements, and the complete normalized task payload (including declared local-change content) for review, without creating or altering the queue.
- `--execute-architect-order` is the local operator's explicit decision to execute the reviewed order; no field inside the incoming JSON can replace that flag.
- `--authorize-local-writes` is separately required for `apply_local_plan` and `apply_known_fix`.
- `--execute-project-code` is separately required for `run_tests` and `run_project_check`. For `run_project_check`, the existing task field `allow_project_code_execution: true` must also be present; both local approval and task policy are enforced.
- All tasks go through the existing action allowlist, dependency graph validation and verification pipeline. Unsupported shell or command actions fail before queue mutation.
- Only tasks in the submitted order are claimed by its execution. External dependencies must already be persisted and succeeded.
- GitHub writes, PR publication and merge are not capabilities of this contract. No automatic merge is introduced.
- The report records `source_is_cryptographically_authenticated: false`, `external_authorization_trusted: false`, and offline/AI-disabled flags. No remote model is called and BOTPYTON itself makes no network request in this operation.
- Explicitly authorized project tests/build scripts may contact networks or produce side effects. The current subprocess runner is not a network sandbox.

## Return states

- `validated_for_review`: a ready packet passed preflight; no queue mutation occurred.
- `needs_input`: the architecture bot must answer the listed questions; no task ran.
- `succeeded`, `failed`, `blocked`, or `incomplete`: execution result from the existing order pipeline. Reports include per-task status, attempts, verification evidence and failure diagnostics.

The order source label is untrusted text. This contract validates the structure and declared intent but does not prove that the architecture bot is honest, that its design is correct, or that the selected project's acceptance criteria are complete. The local operator remains responsible for reviewing scope, permissions, and the target directory.


## Phase 72 — durable architect response and evidence hand-back

A validated order can now produce a separate, machine-readable response file for an external architect to consume. This closes the *file protocol* loop: order in, bounded execution/evidence out, next step encoded in the result. It does not silently connect BotPyton to a model, call an AI API, or autonomously authorize another order.

Validation-only response:

~~~powershell
python -m bot.main --architect-order .cerebro/examples/architect-order.json --architect-response ARCH-ORDER-DEMO-001-response.json
~~~

After reviewing the exact payload and approving its required capabilities, explicitly execute and persist a separate response. For this read-only example no write/code approvals are needed:

~~~powershell
python -m bot.main --architect-order .cerebro/examples/architect-order.json --execute-architect-order --project-root "C:\projects\sample" --memory .cerebro/memory/state.json --architect-response ARCH-ORDER-DEMO-001-execution.json
~~~

The parent directory must already exist and the output path must not exist. BotPyton uses exclusive file creation and refuses to overwrite an earlier response. Choose a unique response filename for each submission. Before starting execution, the file is durably populated with an `execution_in_progress` marker; after the run, BotPyton replaces its contents through the same owned file handle and flushes it to disk. If the process is interrupted, the marker says that execution started and queue mutation is unknown—inspect the queue and any per-task reports before retrying.

Every response includes:

- `response_type` and `schema_version` so a receiver can identify the response contract.
- `order_id`, untrusted source labels, and `request_sha256`: SHA-256 of the exact bounded input bytes that were parsed.
- Final status, whether execution started, queue-mutation state, the local approval flags passed on the command line, and the permissions still required by the task actions.
- Per-task state and attempt summaries plus the existing verification details and report paths returned by the order pipeline.
- A machine-readable `next_action` identifying who should act next (`architect`, `local_operator`, or recovery review) and what step is recommended.
- Explicit safety declarations: packet data cannot grant permissions, BotPyton itself makes no model/API/network request here, and no merge is performed.

Response statuses include `validated_for_review`, `needs_input`, `execution_in_progress`, `succeeded`, `failed`, `blocked`, `incomplete`, and `execution_error`. If the status is `execution_error`, queue mutation is reported as unknown because the failure may have occurred before or after queue persistence; inspect state before resubmitting. The request hash links the response to the exact input bytes but does not authenticate the architect's identity, prove the design correct, or make report files tamper-proof. The receiver should validate the response schema and inspect referenced evidence before planning the next order.


## Phase 73 — on-demand inbox/outbox spool

Use separate, pre-existing folders to hand orders to BOTPYTON and collect responses. This is a one-order-per-invocation spool, **not** a watcher or background service.

~~~powershell
New-Item -ItemType Directory -Force .cerebro\architect-inbox, .cerebro\architect-outbox | Out-Null
# Copy one architect-produced *.json packet into .cerebro\architect-inbox, then:
python -m bot.main --architect-inbox .cerebro\architect-inbox --architect-outbox .cerebro\architect-outbox
~~~

The default mode validates and returns a review response only. After inspecting the packet and its required permissions, an operator may explicitly run:

~~~powershell
python -m bot.main --architect-inbox .cerebro\architect-inbox --architect-outbox .cerebro\architect-outbox --execute-architect-order
~~~

For orders requiring local file changes, separately add `--authorize-local-writes`. For orders that run tests/build scripts, separately add `--execute-project-code`. Do not add permissions simply to clear a blocked status; review the packet first.

Rules:

- Inbox and outbox must already exist, be distinct real directories, and neither may contain the other. The spool never creates folders, polls, recurses into subfolders, or launches a background worker.
- Only top-level regular `.json` files are considered; input size is capped at 600,000 bytes. Selection order is deterministic by filename (case-insensitive). A single invocation processes at most one pending packet.
- Input files are not moved or deleted. Unsafe links/reparse points and oversized or unreadable inputs are skipped and reported; no project task is started from them.
- Each response name includes the SHA-256 of the exact packet bytes plus an authorization profile: `review` or `execute-wX-cY`. A separate profile lets a blocked order be reconsidered after the operator deliberately supplies missing permissions; review and execute responses do not suppress one another.
- A prior response is skipped only if it is a bounded JSON response with the expected `botpyton.architect_response` schema and matching request hash. Malformed or mismatched existing output is an error; it is never overwritten. Each exact order + profile is processed at most once, which prevents accidental replay of side effects.
- Invalid JSON or strict-contract failures receive a durable `rejected_invalid_order` response and no task runs. Missing or surplus permissions receive a clear blocked response and no queue mutation.
- If no pending packet remains, the command returns `idle`. If execution fails or is interrupted, inspect the queue and response evidence before creating a revised packet. Do not replay an unchanged order to repeat side effects.
- The spool makes no network request and calls no AI. BotPyton runs only when explicitly invoked; an external architect must produce/read these files itself.

The spool closes the bounded file protocol for one order at a time. It does not certify an actual external architect identity, nor does it automatically plan, authorize, or submit a following task. See the existing authorization, subprocess/network, and evidence caveats above.


## Phase 74 — recovery after an interrupted execution

If an order's durable response is still `execution_in_progress`, the spool no longer silently treats it as an ordinary completed input. It emits `spool_status: "recovery_required"` and writes a separate immutable recovery response linked to the same request SHA-256. The original in-progress response is left unchanged, and the executor callback is not invoked again.

Recovery responses include a read-only snapshot of the queue records for the task IDs stored in the order's original validation metadata: current state, attempt count, last error, recorded report path, and whether verification metadata is present. If the queue says all tasks succeeded, the next action still asks an operator to inspect reports and verification, because the final response may have failed after queue completion. If a task is running, failed, pending, blocked, or missing—or the queue cannot be read safely—the response makes that condition explicit and asks for reconciliation.

Each recovery report filename is derived from both the original order hash/profile and the queue snapshot hash. A later invocation can record a changed queue state as a new recovery report without overwriting old evidence. Repeating the same recovery snapshot reuses the prior report. Recovery is read-only: it neither calls `resume` nor reruns the order, resets tasks, mutates queue state, modifies the input packet, or removes the original response.

This deliberately favors at-most-once behavior over automatic retries. The operator should inspect the queue/report paths and determine whether side effects completed before preparing any new order.

## Phase 75 — bind interrupted-order recovery to the original request

Recovery now re-parses the exact inbox bytes and revalidates the strict order contract before selecting task IDs from a prior `execution_in_progress` response. The task IDs used for the read-only queue snapshot always come from that canonical order, never from the stored response. BOTPYTON compares the response's order ID, source metadata, task IDs, required local approvals, operator flags and in-progress state markers against the canonical packet and the current invocation profile.

If these fields disagree, BOTPYTON still does not replay any task. It emits `status: "recovery_required"`, marks `evidence.response_binding_status: "mismatch"`, lists only the mismatched field names, and directs the local operator to `inspect_response_integrity`. The original response and inbox packet remain unchanged; the recovery report continues to be content-addressed by the request and queue snapshot.

The request SHA-256 is a correlation digest, **not** a digital signature and does not authenticate the external architect or all local response bytes. This phase detects important mismatches in a persisted interrupted response and prevents its mutable task-ID list from steering recovery. It does not claim tamper-proof storage or replace review of task reports and possible side effects.

## Phase 76 — isolate file-writing architect orders

Any executable architect order that requests `apply_local_plan` or `apply_known_fix` now runs in a dedicated Git worktree instead of changing the selected source checkout. This is mandatory for those actions; the order's dry-run response declares `workspace_policy.required_for_execution: true` without creating a branch, folder, queue or modifying files.

Example:

~~~powershell
python -m bot.main --architect-order order.json --execute-architect-order --authorize-local-writes --project-root "C:\projects\sample" --architect-response response.json
~~~

The default worktree parent is `~/.botpyton/workspaces` (on Windows, beneath the current user's home directory). Override it with `--architect-workspace-parent "D:\BotPytonWorkspaces"`; the selected directory must resolve outside the target source repository. In inbox/outbox mode, supply the same flag alongside `--architect-inbox` and `--architect-outbox` and it is propagated to the child order invocation.

The selected project must be the root of an existing Git repository with a resolvable HEAD and a clean working tree. If Git is unavailable, the source is dirty, or isolation cannot be prepared, BotPyton fails closed before constructing or mutating the task queue; it does not fall back to writing directly into the source checkout. Keep the order file, durable queue/memory, response, and inbox/outbox artifacts outside the target Git repository so those files do not make the source tree dirty before the worktree check. This is especially important when BotPyton is asked to modify its own repository.

The final response carries the local workspace path, branch name, base SHA, and a read-only final inspection (HEAD SHA, working-tree status, diff summary, and whether changes remain). The worktree is a review candidate, not an approved release: BotPyton never commits, pushes, opens a PR, or merges it automatically. Review the diff and verification reports before separately approving publication. If an order also runs tests/build scripts, the separate `--execute-project-code` approval remains mandatory.

For an interrupted execution, recovery may expose the previous workspace locator under `evidence.workspace_claim_from_original_response`, explicitly labeled untrusted. It helps an operator find evidence but is not a signed attestation that those bytes came from that worktree. Keep the no-replay rule: inspect the reported queue, workspace and per-task outputs before deciding how to continue.

## Phase 77 — inline reviewed patches for existing code

A task may now include `reviewed_patch`, a plain unified Git diff, and `allowed_actions: ["apply_reviewed_patch", ...]`. This lets an external architect describe a multi-line, multi-file code edit without sending an arbitrary command to BOTPYTON. The payload is validated by the same constrained patch parser as the standalone reviewed-patch tool.

Limits and restrictions: maximum 250,000 UTF-8 bytes, 20 files and 20,000 lines; ordinary edits to existing tracked files only. Additions, deletions, renames, copies, mode changes, binaries, symlinks, submodules, duplicate paths and unsafe/protected relative paths are rejected. Markdown fences are not accepted. Within one order, reviewed-patch tasks may not overlap on files, and reviewed patches cannot be mixed with `apply_local_plan` or `apply_known_fix`; combine edits for the same file into one patch so preflight stays deterministic.

A reviewed patch requires `--authorize-local-writes` and a clean Git target. BOTPYTON creates the isolated branch/worktree, confirms each patch path is a tracked regular file, and runs `git apply --check` on the exact patch bytes **before it creates or mutates the task queue**. It then applies the same bytes only in that worktree, runs `git diff --check` and bounded static parsing of changed Python, JSON and TOML files, and writes task/action evidence with the patch SHA-256, changed paths and static-check results. The response's workspace record includes patch preflight hashes and final working-tree evidence. A failed preflight can leave a local candidate worktree for inspection, but does not start tasks or mutate the queue; no automatic cleanup deletes evidence.

For example, one task can use `allowed_actions: ["apply_reviewed_patch", "prepare_report"]`, a `reviewed_patch` string, and verification checks `{"type":"patch_applied"}` plus `{"type":"python_syntax"}`. This performs no project-code execution: static parsing and Git operations only. If the order also runs tests/build scripts, it must separately request `run_tests` or `run_project_check` and the operator must separately approve `--execute-project-code`; code executed by a project check is not network-sandboxed.

BOTPYTON does not commit, push, publish a PR, merge, install dependencies, or run shell commands embedded in the diff. The patch target is explicitly required to be a linked `botpyton/*` worktree, not the primary source checkout. Passing the static checks is evidence, not proof of correctness; inspect the candidate diff and reports before authorizing release.
