"""Command-line entry point for BotPyton's deterministic no-AI worker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .memory import JsonMemory, MemoryError
from .planner import order_tasks
from .policy import PolicyError, validate_task
from .queue import QueueError, TaskQueue
from .verifier import verify_python_files


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BotPyton deterministic no-AI worker")
    parser.add_argument("--check", action="store_true", help="validate Python syntax and memory schema")
    parser.add_argument("--validate-task", metavar="PATH", help="validate a JSON task record")
    parser.add_argument("--plan", metavar="PATH", help="validate and order a JSON array of tasks")
    parser.add_argument("--enqueue", metavar="PATH", help="idempotently enqueue one task or a JSON array")
    parser.add_argument("--queue", action="store_true", help="list durable queue records")
    parser.add_argument("--claim-next", action="store_true", help="claim and print the next ready task")
    parser.add_argument("--complete", metavar="TASK_ID", help="mark a running task succeeded")
    parser.add_argument("--result", default=None, help="JSON result for --complete")
    parser.add_argument("--fail", metavar="TASK_ID", help="mark a running task failed")
    parser.add_argument("--error", default="", help="failure reason used with --fail")
    parser.add_argument("--retryable", action="store_true", help="requeue a failure if attempts remain")
    parser.add_argument("--resume", action="store_true", help="recover tasks left running by an interrupted worker")
    parser.add_argument("--memory", default=".cerebro/memory/state.json", help="memory file path")
    args = parser.parse_args(argv)
    try:
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
            record = queue.claim_next()
            _print_json(record)
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
        parser.print_help()
        return 0
    except (OSError, json.JSONDecodeError, PolicyError, MemoryError, QueueError) as exc:
        print(f"CHECK FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
