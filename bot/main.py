"""Command-line entry point for BotPyton's deterministic no-AI worker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .autonomy import AutonomousCycleError, run_autonomous_cycle
from .architect import ArchitectPlanError, build_architecture_plan
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .checkpoint import CheckpointError, export_checkpoint, restore_checkpoint
from .diagnostics import diagnose_failure
from .executor import TaskExecutor
from .github_ops import GitHubClient, GitHubOpsError, submit_change_plan
from .github_preflight import run_github_preflight
from .memory import JsonMemory, MemoryError
from .local_changes import (
    LocalChangeError, apply_local_change_plan, read_local_plan,
    validate_local_change_plan, inspect_local_change_transaction,
    rollback_local_change_transaction,
)
from .orchestration import (
    OrchestrationError,
    submit_change_plan_batch,
    validate_change_plan_batch,
    validate_task_batch,
)
from .order_pipeline import run_order_batch
from .planner import order_tasks
from .repository_analyzer import inspect_repository
from .process_runner import ProcessRunnerError, run_tool_probe
from .tool_registry import discover_tools
from .toolchain_matrix import inspect_toolchain_matrix
from .system_profile import get_system_profile
from .doctor import run_preflight
from .operation_guard import print_pc_result
from .startup_advisor import read_startup_advisor
from .application_cache import preview_application_cache
from .desktop_app_cache import preview_desktop_app_cache
from .application_cache_quarantine import (
    CACHE_QUARANTINE_CONFIRMATION, CACHE_RESTORE_CONFIRMATION,
    preview_application_cache_restore, quarantine_application_cache_candidates,
    restore_application_cache_quarantine,
)
from .pc_care import get_pc_care_report, QUARANTINE_CONFIRMATION, RESTORE_CONFIRMATION, quarantine_cleanup_candidates, restore_quarantined_files, check_application_updates, preview_quarantined_restore, read_pc_care_audit
from .packaging import build_portable_package, verify_portable_archive
from .pc_care import get_pc_care_benchmark, analyze_storage_path, find_duplicate_files, read_device_driver_inventory, read_disk_health_inventory
from .project_checks import ProjectCheckError, discover_project_checks, run_project_check
from .policy import PolicyError, validate_task, validate_relative_path
from .queue import QueueError, TaskQueue
from .verifier import verify_python_files
from .workspace import WorkspaceError, create_isolated_workspace, inspect_workspace


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _read_bounded_json(path_text: str, *, limit_bytes: int = 50_000) -> object:
    # Keep the read bounded even if another process grows the file after stat().
    # Input content is parsed as JSON only; it is never executed as a command.
    try:
        raw = read_bounded_bytes(path_text, limit_bytes=limit_bytes, label="JSON input")
        value = json.loads(raw.decode("utf-8"))
        enforce_json_depth(value, label="JSON input")
        return value
    except (BoundedReadError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise OrchestrationError(f"Invalid or unsafe JSON input: {exc}") from exc


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
    parser.add_argument("--toolchain-matrix", action="store_true",
                        help="emit a bounded read-only project/toolchain matrix; no probes or project code run")
    parser.add_argument("--architect-plan", metavar="BRIEF_JSON",
                        help="turn a bounded programming brief into a deterministic reviewable architecture plan; no code executed")
    parser.add_argument("--architect-ai", metavar="BRIEF_JSON",
                        help="retired: remote model advice is disabled by BOTPYTON's offline policy")
    parser.add_argument("--allow-external-ai", action="store_true",
                        help="retired compatibility flag; external AI/API requests are disabled")
    parser.add_argument("--architect-ai-context", action="append", metavar="RELATIVE_PATH",
                        help="retired compatibility flag; source-context upload is disabled")
    parser.add_argument("--send-source-context", action="store_true",
                        help="retired compatibility flag; source-context upload is disabled")
    parser.add_argument("--architect-ai-failure-report", metavar="REPORT_JSON",
                        help="retired compatibility flag; external failure-report upload is disabled")
    parser.add_argument("--send-failure-report", action="store_true",
                        help="retired compatibility flag; external failure-report upload is disabled")
    parser.add_argument("--system-profile", action="store_true", help="report available RAM, disk space and a conservative child-process memory budget")
    parser.add_argument("--doctor", action="store_true", help="run read-only preflight and show concrete next steps for common setup/resource problems")
    parser.add_argument("--pc-check", action="store_true", help="run the read-only, offline local PC-care audit")
    parser.add_argument("--pc-startup-advisor", action="store_true", help="inspect Windows startup entry targets locally; never disables entries")
    parser.add_argument("--pc-app-cleanup-preview", action="store_true", help="preview old files in recognized browser cache folders; never deletes or changes browser data")
    parser.add_argument("--pc-desktop-cache-preview", action="store_true", help="preview old Discord/Slack/Visual Studio Code cache files only; read-only, no deletion or movement")
    parser.add_argument("--pc-app-cache-quarantine", action="store_true", help="interactively move explicitly selected browser cache files into reversible quarantine")
    parser.add_argument("--pc-app-cache-restore-preview", metavar="MANIFEST_JSON", help="verify a browser-cache quarantine manifest and show restore conflicts; no files moved")
    parser.add_argument("--pc-app-cache-restore", metavar="MANIFEST_JSON", help="restore verified browser-cache files after exact typed confirmation; never overwrite existing files")
    parser.add_argument("--pc-processes", action="store_true", help="show a bounded read-only snapshot of Windows process memory usage")
    parser.add_argument("--pc-cleanup-preview", action="store_true", help="list exact eligible temporary-file candidates without deleting anything")
    parser.add_argument("--pc-cleanup-quarantine", action="store_true", help="move verified temporary-file candidates to reversible quarantine after typed confirmation")
    parser.add_argument("--pc-cleanup-restore", metavar="MANIFEST_JSON", help="restore a BOTPYTON quarantine session without overwriting existing files")
    parser.add_argument("--pc-cleanup-restore-preview", metavar="MANIFEST_JSON", help="dry-run integrity and destination check for a BOTPYTON quarantine manifest")
    parser.add_argument("--pc-audit", action="store_true", help="read the bounded local PC-care operation audit and verify checksums")
    parser.add_argument("--pc-apps", action="store_true", help="list installed Windows application metadata without running uninstallers")
    parser.add_argument("--pc-update-check", action="store_true", help="opt in to a bounded WinGet update-availability listing; never installs or upgrades packages")
    parser.add_argument("--package-portable", metavar="OUTPUT_DIR", help="build a portable source ZIP and SHA-256 sidecar; requires Python 3.11+")
    parser.add_argument("--verify-portable-package", metavar="ZIP_PATH", help="verify a BOTPYTON portable source archive and all member hashes")
    parser.add_argument("--pc-benchmark", action="store_true", help="measure one bounded, read-only PC-care pass and this process resident-memory footprint")
    parser.add_argument("--pc-storage", metavar="FOLDER_PATH", help="analyze storage metadata in one explicitly selected folder; bounded, read-only, no file contents opened")
    parser.add_argument("--pc-duplicates", metavar="FOLDER_PATH", help="find exact duplicate files by SHA-256 in a selected folder; bounded, read-only, never deletes files")
    parser.add_argument("--pc-drivers", action="store_true", help="inventory local Windows device-driver metadata only; never rates or updates drivers")
    parser.add_argument("--pc-disk-health", action="store_true", help="read a bounded local Windows disk status snapshot; no repair or modification")
    parser.add_argument("--tools", action="store_true", help="list installed developer tools without running them")
    parser.add_argument("--probe-tool", metavar="TOOL_ID", help="run the fixed version probe for one registered tool")
    parser.add_argument("--local-plan", metavar="PATH", help="validate a declarative local file-change plan (dry-run by default)")
    parser.add_argument("--execute-local-plan", action="store_true", help="apply --local-plan after hash checks and create backups")
    parser.add_argument("--local-transaction-inspect", metavar="JOURNAL_JSON",
                        help="read-only inspect a durable local-change transaction journal")
    parser.add_argument("--local-transaction-rollback", metavar="JOURNAL_JSON",
                        help="explicitly roll back a local-change transaction only where file identity and SHA-256 still match")
    parser.add_argument("--confirm-transaction-id", metavar="SHA256",
                        help="exact transaction ID confirmation required by --local-transaction-rollback")
    parser.add_argument("--local-agent-config", metavar="PATH", help="validate or use a local-agent JSON config with explicit folder permissions")
    parser.add_argument("--local-agent-validate", action="store_true", help="validate the local-agent config without performing file access")
    parser.add_argument("--local-agent-list", metavar="ROOT_ID", help="list one directory in the configured root; requires --local-agent-config")
    parser.add_argument("--local-agent-read", metavar="ROOT_ID", help="read one bounded UTF-8 file in the configured root")
    parser.add_argument("--local-agent-write", metavar="ROOT_ID", help="write one UTF-8 file; new files are exclusive and replacements require expected SHA-256")
    parser.add_argument("--local-agent-audit", action="store_true", help="verify the local-agent audit hash chain without changing files")
    parser.add_argument("--local-agent-path", default=".", metavar="RELATIVE_PATH", help="canonical path relative to the selected local-agent root (default: . for listing)")
    parser.add_argument("--local-agent-content-stdin", action="store_true", help="read bounded UTF-8 write content from stdin; required with --local-agent-write")
    parser.add_argument("--local-agent-expected-sha256", metavar="SHA256", help="required exact current file hash when replacing an existing target")
    parser.add_argument("--project-root", default=".", help="project directory for local plans and project checks (default: current directory)")
    parser.add_argument("--list-project-checks", action="store_true", help="detect fixed test/build profiles without executing project code")
    parser.add_argument("--run-project-check", metavar="CHECK_ID", help="run one detected check; requires --execute-project-code")
    parser.add_argument("--execute-project-code", action="store_true", help="explicitly authorize execution of project tests/build scripts")
    parser.add_argument("--check-timeout", type=int, default=120, help="seconds allowed for --run-project-check (maximum 300)")
    parser.add_argument("--child-memory-limit-mb", type=int, help="explicit project-check memory cap in MB (64–1024; default adapts to available RAM)")
    parser.add_argument("--create-workspace", metavar="TASK_ID", help="create a Git worktree on a dedicated local branch from a clean project")
    parser.add_argument("--workspace-status", metavar="PATH", help="inspect a worktree's current status and diff summary")
    parser.add_argument("--workspace-parent", metavar="PATH", help="directory outside the source repository for --create-workspace")
    parser.add_argument("--apply-reviewed-patch", metavar="PATCH_PATH", help="validate and apply a reviewed unified diff only inside an isolated Git worktree; never merges or pushes")
    parser.add_argument("--patch-task-id", metavar="TASK_ID", help="safe task identifier required by --apply-reviewed-patch")
    parser.add_argument("--patch-check", metavar="CHECK_ID", help="run one discovered check on baseline and candidate worktrees; requires --execute-project-code")
    parser.add_argument("--validate-task", metavar="PATH", help="validate a JSON task record")
    parser.add_argument("--validate-task-batch", metavar="PATH",
                        help="validate a bounded JSON task object/array without queue mutation")
    parser.add_argument("--plan", metavar="PATH", help="validate and order a JSON array of tasks")
    parser.add_argument("--diagnose", metavar="PATH", help="classify a local error log and gather evidence")
    parser.add_argument("--investigate-failure", metavar="PATH", help="build a bounded static-evidence and falsifiable-hypothesis plan from a failure log")
    parser.add_argument("--investigation-command", metavar="TEXT", help="exact command and arguments that produced the failure (reported as sanitized text only)")
    parser.add_argument("--investigation-exit-code", type=int, metavar="N", help="process exit code from the failed command")
    parser.add_argument("--investigation-expected", metavar="TEXT", help="expected behavior or contract for the failed operation")
    parser.add_argument("--investigation-observed", metavar="TEXT", help="observed behavior and reproducible failure summary")
    parser.add_argument("--run-order", metavar="TASKS_JSON",
                        help="validate, enqueue and execute a bounded task batch with explicit machine-verifiable acceptance checks")
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
    parser.add_argument("--github-preflight", metavar="OWNER/REPO",
                        help="run read-only preflight checks against a real GitHub credential; no writes")
    parser.add_argument("--github-branch", metavar="BRANCH",
                        help="branch to read with --github-preflight (default: repository default branch)")
    parser.add_argument("--github-intent", choices=("inspect", "draft-pr"), default="inspect",
                        help="describe the intended read or draft-PR operation; write permission is never tested by this read-only probe")
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
    if (
        args.architect_ai or args.allow_external_ai or args.architect_ai_context
        or args.send_source_context or args.architect_ai_failure_report or args.send_failure_report
    ):
        _print_json({
            "status": "disabled_offline_policy",
            "error": "External AI and model-provider requests are disabled by BOTPYTON's strict offline policy.",
            "next_step": "Use --architect-plan for deterministic local architecture planning. Legacy adviser flags do not read selected files or reports.",
            "safety": {
                "network_used": False,
                "external_ai_enabled": False,
                "project_files_uploaded": False,
                "failure_report_uploaded": False,
                "project_code_executed": False,
                "commands_executed": False,
                "files_written": False,
            },
        })
        return 2
    try:
        if args.github_preflight:
            allowed_preflight_args = {"github_preflight", "github_branch", "github_intent"}
            conflicts = [
                name.replace("_", "-")
                for name, value in vars(args).items()
                if name not in allowed_preflight_args and value != parser.get_default(name)
            ]
            if conflicts:
                raise GitHubOpsError(
                    "--github-preflight must be a standalone operation; conflicting options: "
                    + ", ".join(sorted(conflicts))
                )
            result = run_github_preflight(
                args.github_preflight,
                branch=args.github_branch,
                intent=args.github_intent,
            )
            _print_json(result)
            return 0 if result["status"] == "ready" else 2
        if args.toolchain_matrix:
            allowed_matrix_args = {"toolchain_matrix", "project_root"}
            conflicts = [
                name.replace("_", "-")
                for name, value in vars(args).items()
                if name not in allowed_matrix_args and value != parser.get_default(name)
            ]
            if conflicts:
                raise OrchestrationError(
                    "--toolchain-matrix must be a standalone operation; conflicting options: "
                    + ", ".join(sorted(conflicts))
                )
            result = inspect_toolchain_matrix(args.project_root)
            _print_json(result)
            return 0 if result["status"] == "discovery_complete" else 2
        if args.github_branch is not None or args.github_intent != "inspect":
            raise GitHubOpsError("--github-branch and --github-intent require --github-preflight")
        if args.run_order:
            ignored_for_order = {
                "run_order", "project_root", "memory", "check_timeout",
                "child_memory_limit_mb", "cycle_max_attempts", "cycle_timeout",
            }
            conflicts = []
            for name, value in vars(args).items():
                if name in ignored_for_order:
                    continue
                if name == "github_intent" and value == "inspect":
                    continue
                if value is None or value is False or value == "" or value == []:
                    continue
                if name == "local_agent_path" and value == ".":
                    continue
                conflicts.append(name.replace("_", "-"))
            if conflicts:
                raise OrchestrationError(
                    "--run-order must be a standalone operation; conflicting options: "
                    + ", ".join(sorted(conflicts))
                )
            order_value = _read_bounded_json(args.run_order, limit_bytes=600_000)
            queue = TaskQueue(JsonMemory(args.memory))
            executor = TaskExecutor(
                Path(args.project_root).expanduser().resolve(),
                test_timeout_seconds=args.check_timeout,
            )
            result = run_order_batch(order_value, queue=queue, executor=executor)
            _print_json(result)
            return 0 if result["status"] == "succeeded" else 1
        if args.apply_reviewed_patch and (
            args.architect_ai or args.architect_ai_failure_report or args.send_failure_report
            or args.architect_plan or args.create_workspace or args.workspace_status
            or args.local_plan or args.local_transaction_inspect or args.local_transaction_rollback
            or args.autonomous_cycle or args.run_project_check
        ):
            raise ValueError("--apply-reviewed-patch must be used as a separate operation")
        if (args.patch_task_id or args.patch_check) and not args.apply_reviewed_patch:
            raise ValueError("--patch-task-id and --patch-check require --apply-reviewed-patch")
        if args.doctor:
            result = run_preflight(args.project_root)
            _print_json(result)
            return 0 if result["ready"] else (2 if result["status"] == "blocked" else 0)
        if args.inspect_repository is not None:
            _print_json(inspect_repository(args.inspect_repository))
            return 0
        if args.architect_plan:
            brief = _read_bounded_json(args.architect_plan, limit_bytes=50_000)
            result = build_architecture_plan(brief)
            _print_json(result)
            return 0 if result["decision"] == "ready_to_plan" else 2
        if args.apply_reviewed_patch:
            # Keep the optional patch engine unloaded during normal BOTPYTON startup.
            from .patch_cycle import PatchCycleError, apply_reviewed_patch
            if not args.patch_task_id:
                raise PatchCycleError("--apply-reviewed-patch requires --patch-task-id TASK_ID")
            if args.execute_project_code and not args.patch_check:
                raise PatchCycleError("--execute-project-code with --apply-reviewed-patch requires --patch-check CHECK_ID")
            if args.patch_check and not args.execute_project_code:
                raise PatchCycleError("--patch-check executes project code; add --execute-project-code only after reviewing the discovered command")
            result = apply_reviewed_patch(
                args.apply_reviewed_patch, args.project_root, args.patch_task_id,
                workspace_parent=args.workspace_parent,
                project_check_id=args.patch_check,
                execute_project_code=args.execute_project_code,
                timeout_seconds=args.check_timeout,
                memory_limit_mb=args.child_memory_limit_mb,
            )
            _print_json(result)
            return 0 if result["status"] in {"candidate_tests_passed", "candidate_ready_for_review"} else 1
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
        if args.pc_startup_advisor:
            return print_pc_result(read_startup_advisor())
        cache_action_count = sum(bool(value) for value in (
            args.pc_app_cache_quarantine, args.pc_app_cache_restore_preview, args.pc_app_cache_restore
        ))
        if cache_action_count > 1:
            raise ValueError("Choose only one browser-cache quarantine or restore action per invocation.")
        if args.pc_app_cache_quarantine:
            preview = preview_application_cache(max_candidates=100, timeout_seconds=5.0)
            if preview.get("status") != "preview_complete" or preview.get("scan_limited") or preview.get("access_errors", 0):
                return print_pc_result(preview)
            candidates = preview.get("candidates", [])
            if not candidates:
                return print_pc_result({
                    "product": "BOTPYTON PC Care", "schema_version": 1,
                    "status": "no_cache_candidates", "operation": "application_cache_quarantine",
                    "read_only": True, "network_used": False, "files_moved": 0,
                    "note": "No eligible files were found by the complete allowlisted browser-cache preview."
                })
            print("BROWSER CACHE QUARANTINE — choose only files you recognize. This moves files; it does not permanently delete them.")
            for index, candidate in enumerate(candidates, start=1):
                print(f"  {index}. [{candidate['size_bytes']} bytes; {candidate['age_days']} days] {candidate['browser']} / {candidate['profile']} / {candidate['cache_area']} — {candidate['path']}")
            print(f"Choose 1–{len(candidates)}. Maximum selected files: 25. Close browsers first; locked files will be skipped.")
            try:
                selection_text = input("Selected item numbers separated by commas (blank cancels): ").strip()
                if not selection_text:
                    return print_pc_result({
                        "product": "BOTPYTON PC Care", "schema_version": 1,
                        "status": "cache_quarantine_blocked", "operation": "application_cache_quarantine",
                        "read_only": True, "network_used": False, "files_moved": 0,
                        "note": "Cancelled at the selection prompt; no files were moved."
                    })
                indices = [int(part.strip()) for part in selection_text.split(",")]
                if (len(indices) > 25 or len(set(indices)) != len(indices)
                        or any(index < 1 or index > len(candidates) for index in indices)):
                    raise ValueError("Selection must contain up to 25 unique displayed item numbers.")
                selected_paths = [candidates[index - 1]["path"] for index in indices]
                print("A fresh full preview, exact metadata comparison, and SHA-256 validation will run before any move.")
                confirmation = input(f'Type exactly "{CACHE_QUARANTINE_CONFIRMATION}" to continue: ')
            except (EOFError, ValueError) as exc:
                return print_pc_result({
                    "product": "BOTPYTON PC Care", "schema_version": 1,
                    "status": "cache_quarantine_blocked", "operation": "application_cache_quarantine",
                    "read_only": True, "network_used": False, "files_moved": 0,
                    "note": f"Selection cancelled or invalid ({type(exc).__name__}); no files were moved."
                })
            result = quarantine_application_cache_candidates(
                selected_paths, confirmation=confirmation
            )
            return print_pc_result(result)
        if args.pc_app_cache_restore_preview:
            return print_pc_result(preview_application_cache_restore(args.pc_app_cache_restore_preview))
        if args.pc_app_cache_restore:
            preview = preview_application_cache_restore(args.pc_app_cache_restore)
            for item in preview.get("items", []):
                if isinstance(item, dict):
                    print(f"  [{item.get('status', 'unknown')}] {item.get('path', '(path missing)')}")
            if preview.get("ready_count", 0) < 1:
                return print_pc_result(preview)
            print("RESTORE PREVIEW — existing destination files will never be overwritten.")
            print(f"Verified files ready to restore: {preview['ready_count']}; blocked/skipped: {preview.get('files_skipped', 0)}")
            try:
                confirmation = input(f'Type exactly "{CACHE_RESTORE_CONFIRMATION}" to continue: ')
            except EOFError:
                confirmation = ""
            result = restore_application_cache_quarantine(
                args.pc_app_cache_restore, confirmation=confirmation
            )
            return print_pc_result(result)
        if args.pc_app_cleanup_preview:
            return print_pc_result(preview_application_cache())
        if args.pc_desktop_cache_preview:
            return print_pc_result(preview_desktop_app_cache())
        if args.pc_apps:
            from .pc_care import read_installed_applications
            return print_pc_result(read_installed_applications())
        if args.pc_update_check:
            return print_pc_result(check_application_updates())
        if args.package_portable:
            _print_json(build_portable_package(args.project_root, args.package_portable))
            return 0
        if args.verify_portable_package:
            _print_json(verify_portable_archive(args.verify_portable_package))
            return 0
        if args.pc_benchmark:
            return print_pc_result(get_pc_care_benchmark())
        if args.pc_storage:
            return print_pc_result(analyze_storage_path(args.pc_storage))
        if args.pc_duplicates:
            return print_pc_result(find_duplicate_files(args.pc_duplicates))
        if args.pc_drivers:
            return print_pc_result(read_device_driver_inventory())
        if args.pc_disk_health:
            return print_pc_result(read_disk_health_inventory())
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
            return print_pc_result(result)
        if args.pc_cleanup_restore_preview:
            return print_pc_result(preview_quarantined_restore(args.pc_cleanup_restore_preview))
        if args.pc_audit:
            return print_pc_result(read_pc_care_audit())
        if args.pc_cleanup_restore:
            manifest_path = Path(args.pc_cleanup_restore).expanduser()
            preview = preview_quarantined_restore(manifest_path)
            print("RESTORE PREVIEW — no existing file will be overwritten.")
            for entry in preview["items"]:
                if isinstance(entry, dict):
                    print(f"  [{entry.get('status', 'unknown')}] {entry.get('path', '(path missing)')}")
            print(
                f"Ready to restore: {preview['ready_count']}; "
                f"metadata-only reconciliations: {preview.get('reconcile_count', 0)}; "
                f"skipped: {preview['files_skipped']}"
            )
            if preview["ready_count"] == 0 and preview.get("reconcile_count", 0) == 0:
                return print_pc_result(preview)
            typed = input(f'Type exactly "{RESTORE_CONFIRMATION}" to restore/reconcile eligible entries: ')
            if typed != RESTORE_CONFIRMATION:
                print("CANCELLED: confirmation did not match; no files were restored.")
                return 2
            result = restore_quarantined_files(manifest_path, confirmation=typed)
            return print_pc_result(result)
        if args.pc_cleanup_preview:
            from .pc_care import get_cleanup_preview
            return print_pc_result(get_cleanup_preview())
        if args.pc_processes:
            from .pc_care import read_top_processes
            return print_pc_result(read_top_processes())
        if args.pc_check:
            return print_pc_result(get_pc_care_report())
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
        local_agent_operations = [
            bool(args.local_agent_validate), args.local_agent_list is not None,
            args.local_agent_read is not None, args.local_agent_write is not None,
            bool(args.local_agent_audit),
        ]
        local_agent_flags_used = (
            args.local_agent_config is not None or any(local_agent_operations)
            or args.local_agent_path != "." or args.local_agent_content_stdin
            or args.local_agent_expected_sha256 is not None
        )
        if local_agent_flags_used:
            from .local_agent import LocalAgent, LocalAgentError, MAX_FILE_BYTES as LOCAL_AGENT_MAX_FILE_BYTES
            if not args.local_agent_config:
                raise LocalAgentError("Local-agent options require --local-agent-config PATH")
            selected_count = sum(local_agent_operations)
            if selected_count > 1:
                raise LocalAgentError("Choose exactly one local-agent operation per invocation")
            if args.local_agent_content_stdin and args.local_agent_write is None:
                raise LocalAgentError("--local-agent-content-stdin requires --local-agent-write")
            if args.local_agent_expected_sha256 is not None and args.local_agent_write is None:
                raise LocalAgentError("--local-agent-expected-sha256 requires --local-agent-write")
            is_path_operation = args.local_agent_list is not None or args.local_agent_read is not None or args.local_agent_write is not None
            if args.local_agent_path != "." and not is_path_operation:
                raise LocalAgentError("--local-agent-path requires --local-agent-list, --local-agent-read, or --local-agent-write")
            agent = LocalAgent(args.local_agent_config)
            if selected_count == 0 or args.local_agent_validate:
                result = {**agent.config_summary(), "audit_integrity": agent.verify_audit()}
            elif args.local_agent_audit:
                result = agent.verify_audit()
            elif args.local_agent_list is not None:
                result = agent.list_directory(args.local_agent_list, args.local_agent_path)
            elif args.local_agent_read is not None:
                if args.local_agent_path == ".":
                    raise LocalAgentError("--local-agent-read requires --local-agent-path RELATIVE_PATH")
                result = agent.read_file(args.local_agent_read, args.local_agent_path)
            else:
                if args.local_agent_path == ".":
                    raise LocalAgentError("--local-agent-write requires --local-agent-path RELATIVE_PATH")
                if not args.local_agent_content_stdin:
                    raise LocalAgentError("--local-agent-write requires --local-agent-content-stdin")
                if hasattr(sys.stdin, "buffer"):
                    raw_content = sys.stdin.buffer.read(LOCAL_AGENT_MAX_FILE_BYTES + 1)
                else:
                    raw_content = sys.stdin.read(LOCAL_AGENT_MAX_FILE_BYTES + 1).encode("utf-8")
                if len(raw_content) > LOCAL_AGENT_MAX_FILE_BYTES:
                    raise LocalAgentError(f"Stdin content exceeds {LOCAL_AGENT_MAX_FILE_BYTES} bytes")
                try:
                    content = raw_content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise LocalAgentError("Stdin content must be valid UTF-8") from exc
                result = agent.write_file(
                    args.local_agent_write, args.local_agent_path, content,
                    expected_sha256=args.local_agent_expected_sha256,
                )
            _print_json(result)
            return 0
        transaction_action_count = sum(bool(value) for value in (
            args.local_transaction_inspect, args.local_transaction_rollback,
        ))
        if transaction_action_count > 1:
            raise LocalChangeError("Choose either --local-transaction-inspect or --local-transaction-rollback, not both")
        if args.confirm_transaction_id and not args.local_transaction_rollback:
            raise LocalChangeError("--confirm-transaction-id requires --local-transaction-rollback")
        if args.local_transaction_rollback and not args.confirm_transaction_id:
            raise LocalChangeError("--local-transaction-rollback requires --confirm-transaction-id SHA256")
        if args.local_transaction_inspect:
            result = inspect_local_change_transaction(
                args.local_transaction_inspect, Path(args.project_root).expanduser().resolve()
            )
            _print_json(result)
            return 0
        if args.local_transaction_rollback:
            result = rollback_local_change_transaction(
                args.local_transaction_rollback, Path(args.project_root).expanduser().resolve(),
                expected_transaction_id=args.confirm_transaction_id,
            )
            _print_json(result)
            return 0 if result["recovery"] in {"rollback_complete", "already_rolled_back"} else 2
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
        investigation_context_used = any(value is not None for value in (
            args.investigation_command, args.investigation_exit_code,
            args.investigation_expected, args.investigation_observed,
        ))
        if investigation_context_used and not args.investigate_failure:
            raise OrchestrationError("Investigation context flags require --investigate-failure PATH")
        if args.investigate_failure and args.diagnose:
            raise OrchestrationError("Choose either --investigate-failure or --diagnose, not both")
        if args.investigate_failure:
            from .unknown_recovery import investigate_failure, _validate_root, MAX_LOG_BYTES
            project_root = _validate_root(args.project_root)
            try:
                relative = validate_relative_path(args.investigate_failure)
                log_path = project_root / relative
                log_raw = read_bounded_bytes(log_path, limit_bytes=MAX_LOG_BYTES, label="Failure log")
                log_text = log_raw.decode("utf-8")
            except (BoundedReadError, UnicodeError) as exc:
                raise OrchestrationError(str(exc)) from exc
            result = investigate_failure(
                log_text, project_root,
                command=args.investigation_command,
                exit_code=args.investigation_exit_code,
                expected=args.investigation_expected,
                observed=args.investigation_observed,
            )
            _print_json(result)
            return 2 if result["status"] == "blocked_attempt_limit" else 0
        if args.diagnose:
            project_root = Path(args.project_root).expanduser().resolve()
            log_path = project_root / validate_relative_path(args.diagnose)
            try:
                log_raw = read_bounded_bytes(log_path, limit_bytes=50_000, label="Diagnostic log")
                log_text = log_raw.decode("utf-8")
            except (BoundedReadError, UnicodeError) as exc:
                raise OrchestrationError(str(exc)) from exc
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
