"""Bounded end-to-end execution of an explicitly supplied task order."""
from __future__ import annotations

from typing import Any

from .executor import TaskExecutor
from .orchestration import OrchestrationError, validate_task_batch
from .queue import TaskQueue

# A report-existence check alone cannot prove that an order's acceptance criteria
# were met. Require at least one check that observes a task-relevant outcome.
_ACCEPTANCE_CHECK_TYPES = frozenset({
    "file_exists", "file_sha256", "python_syntax", "json_valid",
    "tests_pass", "project_check_pass", "local_plan_applied",
})


class OrderPipelineError(OrchestrationError):
    """Raised when an order is unsafe, incomplete, or cannot be executed unambiguously."""


def _normalize_order(value: Any) -> list[dict[str, Any]]:
    summary = validate_task_batch(value)
    tasks = [value] if isinstance(value, dict) else value
    if not isinstance(tasks, list) or not tasks:
        raise OrderPipelineError("An executable order must contain at least one task")
    if summary["count"] != len(tasks):
        raise OrderPipelineError("Order validation count does not match the supplied tasks")

    for task in tasks:
        checks = task.get("verification_checks")
        if not isinstance(checks, list) or not checks:
            raise OrderPipelineError(
                f"Task {task['id']!r} requires machine-verifiable verification_checks"
            )
        if not any(
            isinstance(check, dict) and check.get("type") in _ACCEPTANCE_CHECK_TYPES
            for check in checks
        ):
            raise OrderPipelineError(
                f"Task {task['id']!r} needs at least one substantive acceptance check; "
                "report_exists alone is insufficient"
            )
    return tasks


def validate_order_tasks(value: Any) -> list[dict[str, Any]]:
    """Public, side-effect-free task/order preflight shared by all order adapters."""
    return _normalize_order(value)


def run_order_batch(
    value: Any, *, queue: TaskQueue, executor: TaskExecutor
) -> dict[str, Any]:
    """Validate, persist, execute and verify one bounded task order.

    Only IDs in this order can be claimed by this invocation. Existing external
    dependencies must already be succeeded; unrelated pending queue work is left
    untouched. A failed preflight does not enqueue or execute anything.
    """
    tasks = validate_order_tasks(value)
    target_ids = {task["id"] for task in tasks}
    persisted = {record["id"]: record for record in queue.list_tasks()}

    for task in tasks:
        previous = persisted.get(task["id"])
        if previous and previous["status"] in {"running", "failed", "blocked"}:
            raise OrderPipelineError(
                f"Task {task['id']!r} already has status {previous['status']!r}; "
                "inspect or recover it before submitting this order again"
            )

    external_dependencies = sorted({
        dependency
        for task in tasks
        for dependency in task.get("depends_on", [])
        if dependency not in target_ids
    })
    for dependency in external_dependencies:
        previous = persisted.get(dependency)
        if previous is None:
            raise OrderPipelineError(
                f"External dependency {dependency!r} is not present in the durable queue"
            )
        if previous["status"] != "succeeded":
            raise OrderPipelineError(
                f"External dependency {dependency!r} is {previous['status']!r}, not 'succeeded'"
            )

    # enqueue_many validates the combined persisted dependency graph and its
    # idempotency fingerprints atomically before writing the queue state.
    queue.enqueue_many(tasks)
    attempt_budget = sum(task.get("max_attempts", 3) for task in tasks)
    attempt_results: list[dict[str, Any]] = []

    for _ in range(attempt_budget):
        record = queue.claim_next(allowed_task_ids=target_ids)
        if record is None:
            break
        outcome = executor.execute(record)
        task_id = record["id"]

        if outcome["ok"]:
            queue.complete(task_id, {
                "report_path": outcome.get("report_path"),
                "verification": outcome.get("verification"),
            }, refresh_task_ids=target_ids)
        else:
            diagnostic = outcome.get("diagnostic") or {}
            queue.fail(
                task_id,
                outcome.get("error") or "task_execution_failed",
                retryable=bool(diagnostic.get("retryable")),
                refresh_task_ids=target_ids,
            )

        current = queue.get(task_id)
        attempt_results.append({
            "task_id": task_id,
            "attempt": record.get("attempts", 1),
            "execution_ok": outcome["ok"],
            "queue_status": current["status"],
            "report_path": outcome.get("report_path"),
            "failed_action": outcome.get("failed_action"),
            "diagnostic": outcome.get("diagnostic"),
            "verification": outcome.get("verification"),
        })

    task_results = []
    for task_id in sorted(target_ids):
        current = queue.get(task_id)
        saved_result = current.get("result")
        task_results.append({
            "id": task_id,
            "status": current["status"],
            "attempts": current["attempts"],
            "last_error": current.get("last_error"),
            "report_path": (
                saved_result.get("report_path")
                if isinstance(saved_result, dict) else None
            ),
        })

    statuses = {item["status"] for item in task_results}
    if statuses == {"succeeded"}:
        status = "succeeded"
    elif "failed" in statuses:
        status = "failed"
    elif "blocked" in statuses:
        status = "blocked"
    else:
        status = "incomplete"

    return {
        "schema_version": 1,
        "status": status,
        "task_count": len(tasks),
        "task_ids": [task["id"] for task in tasks],
        "dependency_order": validate_task_batch(tasks)["dependency_order"],
        "attempts_executed": len(attempt_results),
        "attempt_results": attempt_results,
        "tasks": task_results,
        "verification_required": True,
        "unrelated_queue_tasks_claimed": False,
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }
