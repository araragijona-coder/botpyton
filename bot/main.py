"""Command-line entry point for BotPyton's initial safe mode."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .memory import JsonMemory, MemoryError
from .planner import order_tasks
from .policy import PolicyError, validate_task
from .verifier import verify_python_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BotPyton deterministic no-AI worker")
    parser.add_argument("--check", action="store_true", help="validate Python syntax and memory format")
    parser.add_argument("--validate-task", metavar="PATH", help="validate a JSON task record")
    parser.add_argument("--plan", metavar="PATH", help="validate and order a JSON array of tasks")
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
            ordered = order_tasks(tasks)
            print("\n".join(task["id"] for task in ordered))
            return 0
        if args.check:
            errors = verify_python_files(Path.cwd())
            JsonMemory(args.memory).load()
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print("CHECK PASS: Python syntax and memory schema are valid")
            return 0
        parser.print_help()
        return 0
    except (OSError, json.JSONDecodeError, PolicyError, MemoryError) as exc:
        print(f"CHECK FAIL: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
