"""Deterministic dependency ordering; no generated or inferred commands."""
from __future__ import annotations
from typing import Any
from .policy import PolicyError, validate_task


def order_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        validate_task(task)
        task_id = task["id"]
        if task_id in by_id:
            raise PolicyError(f"Duplicate task id: {task_id}")
        by_id[task_id] = task
    for task in tasks:
        unknown = set(task.get("depends_on", [])) - by_id.keys()
        if unknown:
            raise PolicyError(f"Unknown dependencies for {task['id']}: {', '.join(sorted(unknown))}")
        if task["id"] in task.get("depends_on", []):
            raise PolicyError(f"Task cannot depend on itself: {task['id']}")

    pending = {task_id: set(task.get("depends_on", [])) for task_id, task in by_id.items()}
    ordered: list[dict[str, Any]] = []
    while pending:
        ready = sorted(task_id for task_id, deps in pending.items() if not deps)
        if not ready:
            raise PolicyError("Task dependency cycle detected")
        for task_id in ready:
            ordered.append(by_id[task_id])
            pending.pop(task_id)
        for deps in pending.values():
            deps.difference_update(ready)
    return ordered
