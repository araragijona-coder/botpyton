# Architect-order contract — Phase 71

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
