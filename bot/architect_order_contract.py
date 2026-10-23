"""Strict contract for orders produced by an external architecture bot.

The packet is untrusted data. Its source label and task fields do not grant local
permissions; execution and sensitive capabilities are authorized outside this contract.
"""
from __future__ import annotations

import re
from typing import Any

from .orchestration import OrchestrationError, validate_task_batch
from .order_pipeline import validate_order_tasks

MAX_SUMMARY_CHARS = 2_000
MAX_QUESTIONS = 10
MAX_QUESTION_CHARS = 500
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}$")
_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ALLOWED_FIELDS = frozenset({
    "schema_version", "order_id", "source", "summary", "status", "tasks", "questions",
})
_ALLOWED_TASK_FIELDS = frozenset({
    "id", "goal", "depends_on", "allowed_actions", "success_conditions", "max_attempts",
    "verification_checks", "local_change_plan", "allow_project_code_execution",
    "project_check_id", "child_memory_limit_mb", "check_timeout_seconds", "failure_context",
})


class ArchitectOrderError(OrchestrationError):
    """Raised when an external architect order violates the wire contract."""


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ArchitectOrderError(f"{label} must be a non-empty string of at most {limit} characters")
    if value != value.strip():
        raise ArchitectOrderError(f"{label} must not have leading or trailing whitespace")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ArchitectOrderError(f"{label} contains unsupported control characters")
    return value


def _identifier(value: Any, label: str) -> str:
    result = _text(value, label, 100)
    if not _IDENTIFIER.fullmatch(result):
        raise ArchitectOrderError(f"{label} contains characters outside the identifier allowlist")
    return result


def _questions(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_QUESTIONS:
        raise ArchitectOrderError(f"questions must be a list containing 0–{MAX_QUESTIONS} items")
    return [_text(item, f"questions[{index}]", MAX_QUESTION_CHARS)
            for index, item in enumerate(value)]


def validate_architect_order(value: Any) -> dict[str, Any]:
    """Validate an external architect packet without queue mutation or execution.

    Fields carrying claims such as approved, execute, shell, or credentials are
    unknown and rejected rather than trusted.
    """
    if not isinstance(value, dict):
        raise ArchitectOrderError("Architect order must be a JSON object")
    unknown = set(value) - _ALLOWED_FIELDS
    missing = {"schema_version", "order_id", "source", "summary", "status", "tasks"} - set(value)
    if unknown:
        raise ArchitectOrderError("Unknown architect-order fields: " + ", ".join(sorted(map(str, unknown))))
    if missing:
        raise ArchitectOrderError("Missing architect-order fields: " + ", ".join(sorted(missing)))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ArchitectOrderError("schema_version must be integer 1")

    order_id = _text(value["order_id"], "order_id", 80)
    if not _ORDER_ID.fullmatch(order_id):
        raise ArchitectOrderError("order_id must use letters, digits, dot, underscore, or hyphen")

    source = value["source"]
    if not isinstance(source, dict):
        raise ArchitectOrderError("source must be an object containing agent_id and optional plan_id")
    unknown_source = set(source) - {"agent_id", "plan_id"}
    missing_source = {"agent_id"} - set(source)
    if unknown_source or missing_source:
        details = []
        if missing_source:
            details.append("missing: " + ", ".join(sorted(missing_source)))
        if unknown_source:
            details.append("unknown: " + ", ".join(sorted(map(str, unknown_source))))
        raise ArchitectOrderError("Invalid source metadata (" + "; ".join(details) + ")")
    clean_source = {"agent_id": _identifier(source["agent_id"], "source.agent_id")}
    if "plan_id" in source:
        clean_source["plan_id"] = _identifier(source["plan_id"], "source.plan_id")

    summary = _text(value["summary"], "summary", MAX_SUMMARY_CHARS)
    status = value["status"]
    if not isinstance(status, str) or status not in {"ready", "needs_input"}:
        raise ArchitectOrderError("status must be 'ready' or 'needs_input'")
    tasks_value = value["tasks"]
    if not isinstance(tasks_value, list):
        raise ArchitectOrderError("tasks must be a JSON array")
    questions = _questions(value.get("questions"))

    if status == "needs_input":
        if tasks_value:
            raise ArchitectOrderError("needs_input orders must not contain executable tasks")
        if not questions:
            raise ArchitectOrderError("needs_input orders must provide at least one specific question")
        tasks: list[dict[str, Any]] = []
        dependency_order: list[str] = []
        external_dependencies: list[str] = []
    else:
        if not tasks_value:
            raise ArchitectOrderError("ready orders must contain at least one task")
        if questions:
            raise ArchitectOrderError("ready orders must not contain unresolved questions")
        for index, task in enumerate(tasks_value):
            if isinstance(task, dict):
                unknown_task_fields = set(task) - _ALLOWED_TASK_FIELDS
                if unknown_task_fields:
                    raise ArchitectOrderError(
                        f"Task {index} has unknown fields: "
                        + ", ".join(sorted(map(str, unknown_task_fields)))
                    )
        try:
            tasks = validate_order_tasks(tasks_value)
            batch = validate_task_batch(tasks)
        except (OrchestrationError, ValueError, TypeError) as exc:
            raise ArchitectOrderError(f"Task batch failed strict validation: {exc}") from exc
        dependency_order = batch["dependency_order"]
        external_dependencies = batch["external_dependencies"]

    actions = {action for task in tasks for action in task.get("allowed_actions", [])}
    approvals: list[str] = []
    if actions.intersection({"apply_local_plan", "apply_known_fix"}):
        approvals.append("local_file_write")
    if actions.intersection({"run_tests", "run_project_check"}):
        approvals.append("project_code_execution")

    return {
        "schema_version": 1,
        "order_id": order_id,
        "source": clean_source,
        "summary": summary,
        "status": status,
        "questions": questions,
        "task_count": len(tasks),
        "task_ids": [task["id"] for task in tasks],
        "dependency_order": dependency_order,
        "external_dependencies": external_dependencies,
        "required_local_approvals": approvals,
        "normalized_tasks": tasks,
        "source_is_cryptographically_authenticated": False,
        "external_authorization_trusted": False,
        "packet_can_enable_execution": False,
        "github_writes_allowed": False,
        "automatic_merge": False,
        "network_used_by_botpyton": False,
        "project_code_network_isolation": (
            "not_enforced_when_project_code_is_explicitly_authorized"
            if "project_code_execution" in approvals else "not_applicable_to_this_order"
        ),
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }
