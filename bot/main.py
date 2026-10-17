"""Command-line entry point for BotPyton's deterministic no-AI worker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .autonomy import AutonomousCycleError, run_autonomous_cycle
from .checkpoint import CheckpointError, export_checkpoint, restore_checkpoint
from .diagnostics import diagnose_failure
from .executor import TaskExecutor
from .github_ops import GitHubClient, GitHubOpsError, submit_change_plan
from .memory import JsonMemory, MemoryError
from .local_changes import LocalChangeError, apply_local_change_plan, read_local_plan, validate_local_change_plan
from .orchestration import (
    OrchestrationError,
    submit_change_plan_batch,
    validate_change_plan_batch,
    validate_task_batch,
)
from .planner import order_tasks
from .repository_analyzer import inspect_repository
from .process_runner import ProcessRunnerError, run_tool_probe
from .tool_registry import discover_tools
from .system_profile import get_system_profile
from .pc_care import get_pc_care_report, QUARANTINE_CONFIRMATION, RESTORE_CONFIRMATION, quarantine_cleanup_candidates, restore_quarantined_files, check_application_updates, preview_quarantined_restore, read_pc_care_audit
from .project_checks import ProjectCheckError, discover_project_checks, run_project_check
from .policy import PolicyError, validate_task, validate_relative_path
from .queue import QueueError, TaskQueue
from .verifier import verify_python_files
from .workspace import WorkspaceError, create_isolated_workspace, inspect_workspace


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _read_bounded_json(path_text: str, *, limit_bytes: int = 50_000) -> object:
    # This helper only reads bounded JSON input; workflow callers use RUNNER_TEMP
    # absolute paths. No input content is executed or used as a shell command.
    path = Path(path_text)
    if path.stat().st_size > limit_bytes:
        raise OrchestrationError(f"JSON input exceeds {limit_bytes} bytes")
    raw = path.read_bytes()
    if len(raw) > limit_bytes:
        raise OrchestrationError(f"JSON input exceeds {limit_bytes} bytes")
    return json.loads(raw.decode("utf-8"))


def _run_claimed(queue: TaskQueue, record: dict, executor: TaskExecutor) -> dict:
    task_id = record["id"]
    outcome = executor.execute(record)
    if outcome["ok"]:
        queue.complete(task_id, {
            "report_path": outcome["report_path"],
            "actions": outcome["actions"],
        })
    else:
        diagnostic = outcome.get("diagnostic") or {}
        queue.fail(
            task_id, outcome.get("error") or "task_execution_failed",
            retryable=bool(diagnostic.get("retryable")),
        )
    return {
        "task_id": task_id,
        "execution_ok": outcome["ok"],
        "queue_status": queue.get(task_id)["status"],
        "report_path": outcome["report_path"],
        "failed_action": outcome.get("failed_action"),
        "diagnostic": outcome.get("diagnostic"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BotPyton deterministic no-AI worker")
    parser.add_argument("--check", action="store_true", help="validate Python syntax and memory schema")
    parser.add_argument("--inspect-repository", metavar="PATH", nargs="?", const=".",
                        help="bounded read-only project inventory; candidate checks are not executed")
    parser.add_argument("--system-profile", action="store_true", help="report available RAM, disk space and a conservative child-process memory budget")
    parser.add_argument("--pc-check", action="store_true", help="run the read-only, offline local PC-care audit")
    parser.add_argument("--pc-processes", action="store_true", help="show a bounded read-only snapshot of Windows process memory usage")
    parser.add_argument("--pc-cleanup-preview", action="store_true", help="list exact eligible temporary-file candidates without deleting anything")
    parser.add_argument("--pc-cleanup-quarantine", action="store_true", help="move verified temporary-file candidates to reversible quarantine after typed confirmation")
    parser.add_argument("--pc-cleanup-restore", metavar="MANIFEST_JSON", help="restore a BOTPYTON quarantine session without overwriting existing files")
    parser.add_argument("--pc-cleanup-restore-preview", metavar="MANIFEST_JSON", help="dry-run integrity and destination check for a BOTPYTON quarantine manifest")
    parser.add_argument("--pc-audit", action="store_true", help="read the bounded local PC-care operation audit and verify checksums")
    parser.add_argument("--pc-apps", action="store_true", help="list installed Windows application metadata without running uninstallers")
    parser.add_argument("--pc-update-check", action="store_true", help="opt in to a bounded WinGet update-availability listing; never installs or upgrades packages")
    parser.add_argument("--tools", action="store_true", help="list installed developer tools without running them")
    parser.add_argument("--probe-tool", metavar="TOOL_ID", help="run the fixed version probe for one registered tool")
    parser.add_argument("--local-plan", metavar="PATH", help="validate a declarative local file-change plan (dry-run by default)")
    parser.add_argument("--execute-local-plan", action="store_true", help="apply --local-plan after hash checks and create backups")
    parser.add_argument("--project-root", default=".", help="project directory for local plans and project checks (default: current directory)")
    parser.add_argument("--list-project-checks", action="store_true", help="detect fixed test/build profiles without executing project code")
    parser.add_argument("--run-project-check", metavar="CHECK_ID", help="run one detected check; requires --execute-project-code")
    parser.add_argument("--execute-project-code", action="store_true", help="explicitly authorize execution of project tests/build scripts")
    parser.add_argument("--check-timeout", type=int, default=120, help="seconds allowed for --run-project-check (maximum 300)")
    parser.add_argument("--child-memory-limit-mb", type=int, help="explicit project-check memory cap in MB (64–1024; default adapts to available RAM)")
    parser.add_argument("--create-workspace", metavar="TASK_ID", help="create a Git worktree on a dedicated local branch from a clean project")
    parser.add_argument("--workspace-status", metavar="PATH", help="inspect a worktree's current status and diff summary")
    parser.add_argument("--workspace-parent", metavar="PATH", help="directory outside the source repository for --create-workspace")
    parser.add_argument("--validate-task", metavar="PATH", help="validate a JSON task record")
    parser.add_argument("--validate-task-batch", metavar="PATH",
                        help="validate a bounded JSON task object/array without queue mutation")
    parser.add_argument("--plan", metavar="PATH", help="validate and order a JSON array of tasks")
    parser.add_argument("--diagnose", metavar="PATH", help="classify a local error log and gather evidence")
    parser.add_argument("--enqueue", metavar="PATH", help="idempotently enqueue one task or a JSON array")
    parser.add_argument("--queue", action="store_true", help="list durable queue records")
    parser.add_argument("--claim-next", action="store_true", help="claim and print the next ready task")
    parser.add_argument("--complete", metavar="TASK_ID", help="mark a running task succeeded")
    parser.add_argument("--result", default=None, help="JSON result for --complete")
    parser.add_argument("--fail", metavar="TASK_ID", help="mark a running task failed")
    parser.add_argument("--error", default="", help="failure reason used with --fail")
    parser.add_argument("--retryable", action="store_true", help="requeue a failure if attempts remain")
    parser.add_argument("--resume", action="store_true", help="recover tasks left running by an interrupted worker")
    parser.add_argument("--work-once", action="store_true", help="execute one ready queued task")
    parser.add_argument("--work-queue", nargs="?", const=10, type=int, metavar="LIMIT",
                        help="execute up to LIMIT queued attempts (default: 10)")
    parser.add_argument("--export-checkpoint", metavar="DIR",
                        help="write validated state.json and SHA-256 manifest for GitHub artifact storage")
    parser.add_argument("--restore-checkpoint", metavar="DIR",
                        help="verify and restore state.json plus manifest downloaded from GitHub")
    parser.add_argument("--github-plan", metavar="PATH",
                        help="validate a declarative GitHub change plan (dry-run by default)")
    parser.add_argument("--execute-github-plan", action="store_true",
                        help="execute branch, one commit and draft pull-request operations for --github-plan")
    parser.add_argument("--github-plans", metavar="PATH",
                        help="validate a JSON array of human-review change plans (dry-run by default)")
    parser.add_argument("--execute-github-plans", action="store_true",
                        help="execute the prevalidated draft-only plan batch; requires --github-plans")
    parser.add_argument("--autonomous-cycle", action="store_true",
                        help="run bounded test, exact known repair, rerun, verification, and audit reports")
    parser.add_argument("--cycle-max-attempts", type=int, default=3,
                        help="maximum test attempts for --autonomous-cycle (hard maximum: 3)")
    parser.add_argument("--cycle-timeout", type=int, default=120,
                        help="seconds per fixed test attempt for --autonomous-cycle (hard maximum: 120)")
    parser.add_argument("--memory", default=".cerebro/memory/state.json", help="memory file path")
    args = parser.parse_args(argv)
    try:
        if args.inspect_repository is not None:
            _print_json(inspect_repository(args.inspect_repository))
            return 0
        if args.create_workspace and args.workspace_status:
            raise WorkspaceError("Use either --create-workspace or --workspace-status, not both")
        if args.workspace_parent and not args.create_workspace:
            raise WorkspaceError("--workspace-parent requires --create-workspace")
        if args.create_workspace:
            _print_json(create_isolated_workspace(
                args.project_root, args.create_workspace, workspace_parent=args.workspace_parent
            ))
            return 0
        if args.workspace_status:
            _print_json(inspect_workspace(args.workspace_status))
            return 0
        if args.pc_apps:
            from .pc_care import read_installed_applications
            _print_json(read_installed_applications())
            return 0
        if args.pc_update_check:
            _print_json(check_application_updates())
            return 0
        if args.pc_cleanup_quarantine and args.pc_cleanup_restore:
            raise ValueError("Choose either --pc-cleanup-quarantine or --pc-cleanup-restore, not both")
        if args.pc_cleanup_quarantine:
            from .pc_care import get_cleanup_preview, MAX_QUARANTINE_CANDIDATES
            preview = get_cleanup_preview(max_candidates=MAX_QUARANTINE_CANDIDATES)
            print("REVERSIBLE CLEANUP — review every exact path below.")
            print(f"Preview status: {preview['status']}; candidates: {preview['candidate_count']}; estimated bytes: {preview['candidate_bytes']}")
            for candidate in preview["candidates"]:
                print(f"  [{candidate['size_bytes']} bytes] {candidate['path']}")
            if preview["status"] != "preview_complete":
                _print_json({"status": "cancelled_partial_preview", "files_quarantined": 0, "preview": preview})
                return 2
            if not preview["candidates"]:
                _print_json({"status": "no_candidates", "files_quarantined": 0, "message": "No eligible old temporary files were found."})
                return 0
            print("Nothing will be permanently deleted. Files are moved to a quarantine folder.")
            typed = input(f'Type exactly "{QUARANTINE_CONFIRMATION}" to continue: ')
            if typed != QUARANTINE_CONFIRMATION:
                print("CANCELLED: confirmation did not match; no files were moved.")
                return 2
            result = quarantine_cleanup_candidates(preview, confirmation=typed)
            _print_json(result)
            return 0 if result["status"] in {"completed", "no_candidates", "partial"} else 1
        if args.pc_cleanup_restore_preview:
            _print_json(preview_quarantined_restore(args.pc_cleanup_restore_preview))
            return 0
        if args.pc_audit:
            _print_json(read_pc_care_audit())
            return 0
        if args.pc_cleanup_restore:
            manifest_path = Path(args.pc_cleanup_restore).expanduser()
            preview = preview_quarantined_restore(manifest_path)
            print("RESTORE PREVIEW — no existing file will be overwritten.")
            for entry in preview["items"]:
                if isinstance(entry, dict):
                    print(f"  [{entry.get('status', 'unknown')}] {entry.get('path', '(path missing)')}")
            print(f"Ready to restore: {preview['ready_count']}; skipped: {preview['files_skipped']}")
            if preview["ready_count"] == 0:
                _print_json(preview)
                return 0
            typed = input(f'Type exactly "{RESTORE_CONFIRMATION}" to restore ready entries: ')
            if typed != RESTORE_CONFIRMATION:
                print("CANCELLED: confirmation did not match; no files were restored.")
                return 2
            result = restore_quarantined_files(manifest_path, confirmation=typed)
            _print_json(result)
            return 0 if result["status"] in {"restored", "no_files_restored"} else 1
        if args.pc_cleanup_preview:
            from .pc_care import get_cleanup_preview
            _print_json(get_cleanup_preview())
            return 0
        if args.pc_processes:
            from .pc_care import read_top_processes
            _print_json(read_top_processes())
            return 0
        if args.pc_check:
            _print_json(get_pc_care_report())
            return 0
        if args.system_profile:
            _print_json(get_system_profile(Path.cwd()))
            return 0
        if args.tools:
            _print_json(discover_tools())
            return 0
        if args.list_project_checks:
            _print_json(discover_project_checks(args.project_root))
            return 0
        if args.execute_project_code and not args.run_project_check:
            raise ProjectCheckError("--execute-project-code requires --run-project-check")
        if args.run_project_check:
            result = run_project_check(
                args.run_project_check, args.project_root,
                execute_project_code=args.execute_project_code,
                timeout_seconds=args.check_timeout,
                memory_limit_mb=args.child_memory_limit_mb,
            )
            _print_json(result)
            return 0 if result["ok"] else 1
        if args.probe_tool:
            _print_json(run_tool_probe(args.probe_tool, root=Path.cwd()))
            return 0
        if args.execute_local_plan and not args.local_plan:
            raise LocalChangeError("--execute-local-plan requires --local-plan")
        if args.local_plan:
            plan_data = read_local_plan(args.local_plan)
            project_root = Path(args.project_root).expanduser().resolve()
            result = (apply_local_change_plan(plan_data, project_root)
                      if args.execute_local_plan
                      else validate_local_change_plan(plan_data, project_root))
            _print_json(result)
            return 0
        if args.autonomous_cycle:
            result = run_autonomous_cycle(
                Path(args.project_root).expanduser().resolve(),
                max_attempts=args.cycle_max_attempts,
                test_timeout_seconds=args.cycle_timeout,
            )
            _print_json(result)
            return 0 if result["status"] == "succeeded" else 1
        if args.execute_github_plan and not args.github_plan:
            raise GitHubOpsError("--execute-github-plan requires --github-plan")
        if args.execute_github_plans and not args.github_plans:
            raise GitHubOpsError("--execute-github-plans requires --github-plans")
        if args.github_plan and args.github_plans:
            raise GitHubOpsError("Use either --github-plan or --github-plans, not both")
        if args.github_plans:
            plans_data = _read_bounded_json(args.github_plans)
            validated_batch = validate_change_plan_batch(plans_data)
            client = GitHubClient()
            result = submit_change_plan_batch(
                validated_batch["plans"], client=client, execute=args.execute_github_plans
            )
            _print_json(result)
            return 0
        if args.github_plan:
            plan_path = Path(validate_relative_path(args.github_plan))
            plan_data = _read_bounded_json(str(plan_path), limit_bytes=600_000)
            client = GitHubClient()
            result = submit_change_plan(plan_data, client=client, execute=args.execute_github_plan)
            _print_json(result)
            return 0
        if args.export_checkpoint:
            _print_json(export_checkpoint(args.memory, args.export_checkpoint))
            return 0
        if args.restore_checkpoint:
            _print_json(restore_checkpoint(args.restore_checkpoint, args.memory))
            return 0
        if args.validate_task_batch:
            result = validate_task_batch(_read_bounded_json(args.validate_task_batch))
            _print_json(result)
            return 0
        if args.validate_task:
            task = _read_bounded_json(args.validate_task, limit_bytes=600_000)
            if not isinstance(task, dict):
                raise PolicyError("Task file must contain a JSON object")
            validate_task(task)
            print(f"VALID task={task['id']}")
            return 0
        if args.plan:
            tasks = _read_bounded_json(args.plan, limit_bytes=600_000)
            if not isinstance(tasks, list):
                raise PolicyError("Plan file must contain a JSON array")
            print("\n".join(task["id"] for task in order_tasks(tasks)))
            return 0
        if args.diagnose:
            project_root = Path(args.project_root).expanduser().resolve()
            log_path = project_root / validate_relative_path(args.diagnose)
            if log_path.stat().st_size > 50_000:
                raise OrchestrationError("Diagnostic log exceeds 50000 bytes")
            log_text = log_path.read_text(encoding="utf-8")
            if len(log_text.encode("utf-8")) > 50_000:
                raise OrchestrationError("Diagnostic log exceeds 50000 bytes")
            _print_json(diagnose_failure(log_text, project_root))
            return 0
        if args.check:
            errors = verify_python_files(Path(args.project_root).expanduser().resolve())
            JsonMemory(args.memory).load()
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print("CHECK PASS: Python syntax and memory schema are valid")
            return 0

        queue = TaskQueue(JsonMemory(args.memory))
        if args.enqueue:
            incoming = _read_bounded_json(args.enqueue, limit_bytes=600_000)
            tasks = incoming if isinstance(incoming, list) else [incoming]
            _print_json([{"id": record["payload"]["id"], "status": record["status"],
                          "idempotency_key": record["idempotency_key"]}
                         for record in queue.enqueue_many(tasks)])
            return 0
        if args.queue:
            _print_json(queue.list_tasks())
            return 0
        if args.claim_next:
            _print_json(queue.claim_next())
            return 0
        if args.complete:
            result = json.loads(args.result) if args.result is not None else None
            _print_json(queue.complete(args.complete, result))
            return 0
        if args.fail:
            _print_json(queue.fail(args.fail, args.error, retryable=args.retryable))
            return 0
        if args.resume:
            _print_json({"resumed": queue.resume_interrupted(), "tasks": queue.list_tasks()})
            return 0
        if args.work_once or args.work_queue is not None:
            limit = 1 if args.work_once else args.work_queue
            if limit is None or limit < 1 or limit > 100:
                raise QueueError("worker limit must be between 1 and 100")
            executor = TaskExecutor(Path(args.project_root).expanduser().resolve())
            outcomes = []
            for _ in range(limit):
                record = queue.claim_next()
                if record is None:
                    break
                outcomes.append(_run_claimed(queue, record, executor))
                if args.work_once:
                    break
            remaining = queue.list_tasks()
            _print_json({
                "attempts_executed": len(outcomes),
                "outcomes": outcomes,
                "tasks": [{"id": item["id"], "status": item["status"]} for item in remaining],
                "ai_enabled": False,
                "external_ai_apis_enabled": False,
            })
            terminal_bad = any(item["status"] in {"failed", "blocked", "running"} for item in remaining)
            pending_after_limit = bool(args.work_queue is not None and any(
                item["status"] == "pending" for item in remaining
            ))
            if args.work_once and outcomes and not outcomes[0]["execution_ok"]:
                return 1
            return 1 if terminal_bad or pending_after_limit else 0
        parser.print_help()
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyError, MemoryError,
            QueueError, CheckpointError, GitHubOpsError, AutonomousCycleError, ProcessRunnerError, LocalChangeError, ProjectCheckError, ValueError, EOFError) as exc:
        print(f"CHECK FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
