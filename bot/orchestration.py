"""Batch validation and draft-PR preparation for JSON-driven GitHub runs.

This module is deliberately deterministic: task text is data, not executable input.
"""
from __future__ import annotations

from typing import Any

from .github_ops import GitHubClient, GitHubOpsError, submit_change_plan, validate_change_plan
from .planner import order_tasks
from .policy import PolicyError, validate_task

MAX_TASKS_PER_BATCH = 25
MAX_CHANGE_PLANS_PER_BATCH = 10


class OrchestrationError(ValueError):
    """Raised when a declarative task or plan batch is unsafe or ambiguous."""


def validate_task_batch(value: Any) -> dict[str, Any]:
    """Validate all incoming tasks before any persistent queue mutation."""
    if isinstance(value, dict):
        tasks = [value]
    elif isinstance(value, list):
        tasks = value
    else:
        raise OrchestrationError("Task batch must be a JSON task object or array")
    if len(tasks) > MAX_TASKS_PER_BATCH:
        raise OrchestrationError(f"Task batch exceeds the {MAX_TASKS_PER_BATCH}-task limit")
    if any(not isinstance(task, dict) for task in tasks):
        raise OrchestrationError("Every task in the batch must be a JSON object")

    seen: set[str] = set()
    for task in tasks:
        validate_task(task)
        task_id = task["id"]
        if task_id in seen:
            raise OrchestrationError(f"Duplicate task ID in incoming batch: {task_id}")
        seen.add(task_id)

    # Dependencies may refer to tasks already persisted by earlier workflow runs.
    # Validate ordering/cycles inside this batch, while final dependency existence is
    # checked atomically by TaskQueue.enqueue_many against the complete persisted queue.
    local_ids = {task["id"] for task in tasks}
    local_graph = [
        {**task, "depends_on": [dep for dep in task.get("depends_on", []) if dep in local_ids]}
        for task in tasks
    ]
    ordered = order_tasks(local_graph) if local_graph else []
    external_dependencies = sorted({
        dep for task in tasks for dep in task.get("depends_on", []) if dep not in local_ids
    })
    return {
        "schema_version": 1,
        "count": len(tasks),
        "task_ids": [task["id"] for task in tasks],
        "dependency_order": [task["id"] for task in ordered],
        "external_dependencies": external_dependencies,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }


def _canonical_plan(plan: Any) -> dict[str, Any]:
    normalized = validate_change_plan(plan)
    if not normalized.draft:
        raise OrchestrationError(
            f"Plan {normalized.task_id!r} must set draft=true for human review"
        )
    return {
        "schema_version": 1,
        "task_id": normalized.task_id,
        "base_branch": normalized.base_branch,
        "title": normalized.title,
        "body": normalized.body,
        "commit_message": normalized.commit_message,
        "draft": True,
        "changes": [
            {"path": path, "content": normalized.changes[path]}
            for path in sorted(normalized.changes)
        ],
    }


def validate_change_plan_batch(value: Any) -> dict[str, Any]:
    """Validate every PR plan locally before any GitHub write operation."""
    if not isinstance(value, list):
        raise OrchestrationError("Change-plan batch must be a JSON array")
    if len(value) > MAX_CHANGE_PLANS_PER_BATCH:
        raise OrchestrationError(
            f"Change-plan batch exceeds the {MAX_CHANGE_PLANS_PER_BATCH}-plan limit"
        )
    canonical: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    branches: set[str] = set()
    total_bytes = 0
    for plan in value:
        clean = _canonical_plan(plan)
        validated = validate_change_plan(clean)
        if clean["task_id"] in task_ids:
            raise OrchestrationError(f"Duplicate change-plan task_id: {clean['task_id']}")
        if validated.branch_name in branches:
            raise OrchestrationError("Two plans resolve to the same deterministic branch")
        task_ids.add(clean["task_id"])
        branches.add(validated.branch_name)
        total_bytes += sum(len(item["content"].encode("utf-8")) for item in clean["changes"])
        canonical.append(clean)
    if total_bytes > 1_000_000:
        raise OrchestrationError("Combined change-plan payload exceeds 1,000,000 bytes")
    return {
        "schema_version": 1,
        "count": len(canonical),
        "plans": canonical,
        "task_ids": [plan["task_id"] for plan in canonical],
        "total_bytes": total_bytes,
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }


def submit_change_plan_batch(
    value: Any, *, client: GitHubClient, execute: bool = False
) -> dict[str, Any]:
    """Submit validated plans one by one; dry-run by default and draft-only."""
    validated = validate_change_plan_batch(value)
    results = [
        submit_change_plan(plan, client=client, execute=execute)
        for plan in validated["plans"]
    ]
    return {
        "schema_version": 1,
        "mode": "executed" if execute else "dry_run",
        "count": len(results),
        "results": results,
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }
