"""Command-line entry point for BotPyton's deterministic no-AI worker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checkpoint import CheckpointError, export_checkpoint, restore_checkpoint
from .diagnostics import diagnose_failure
from .executor import TaskExecutor
from .github_ops import GitHubClient, GitHubOpsError, submit_change_plan
from .memory import JsonMemory, MemoryError
from .planner import order_tasks
from .policy import PolicyError, validate_task, validate_relative_path
from .queue import QueueError, TaskQueue
from .verifier import verify_python_files


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


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
    parser.add_argument("--validate-task", metavar="PATH", help="validate a JSON task record")
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
    parser.add_argument("--memory", default=".cerebro/memory/state.json", help="memory file path")
    args = parser.parse_args(argv)
    try:
        if args.execute_github_plan and not args.github_plan:
            raise GitHubOpsError("--execute-github-plan requires --github-plan")
        if args.github_plan:
            plan_path = Path(validate_relative_path(args.github_plan))
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
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
        if args.validate_task:
            task = json.loads(Path(args.validate_task).read_text(encoding="utf-8"))
            validate_task(task)
            print(f"VALID task={task['id']}")
            return 0
        if args.plan:
            tasks = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if not isinstance(tasks, list):
                raise PolicyError("Plan file must contain a JSON array")
            print("\n".join(task["id"] for task in order_tasks(tasks)))
            return 0
        if args.diagnose:
            log_path = Path(validate_relative_path(args.diagnose))
            log_text = log_path.read_text(encoding="utf-8")
            _print_json(diagnose_failure(log_text, Path.cwd()))
            return 0
        if args.check:
            errors = verify_python_files(Path.cwd())
            JsonMemory(args.memory).load()
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print("CHECK PASS: Python syntax and memory schema are valid")
            return 0

        queue = TaskQueue(JsonMemory(args.memory))
        if args.enqueue:
            incoming = json.loads(Path(args.enqueue).read_text(encoding="utf-8"))
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
            executor = TaskExecutor(Path.cwd())
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
            QueueError, CheckpointError, GitHubOpsError, ValueError) as exc:
        print(f"CHECK FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
