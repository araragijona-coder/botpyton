"""Safety policy for task records and deterministic operations."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

ALLOWED_ACTIONS = frozenset({
    "inspect_repository",
    "validate_task",
    "run_tests",
    "run_project_check",
    "apply_known_fix",
    "apply_local_plan",
    "prepare_report",
})
PROTECTED_PATH_PREFIXES = (
    ".github/workflows/",
    ".cerebro/policies/",
)

class PolicyError(ValueError):
    """Raised when a task violates the local safety policy."""


def validate_task(task: dict[str, Any]) -> None:
    required = {"id", "goal", "allowed_actions", "success_conditions"}
    missing = required - task.keys()
    if missing:
        raise PolicyError(f"Missing required task fields: {', '.join(sorted(missing))}")
    if not isinstance(task["id"], str) or not task["id"].strip():
        raise PolicyError("Task id must be a non-empty string")
    if not isinstance(task["goal"], str) or not task["goal"].strip():
        raise PolicyError("Task goal must be a non-empty string")
    actions = task["allowed_actions"]
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        raise PolicyError("allowed_actions must be a list of strings")
    unknown = set(actions) - ALLOWED_ACTIONS
    if unknown:
        raise PolicyError(f"Actions not allowed by policy: {', '.join(sorted(unknown))}")
    if not isinstance(task["success_conditions"], list) or not task["success_conditions"]:
        raise PolicyError("success_conditions must be a non-empty list")
    attempts = task.get("max_attempts", 3)
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 3:
        raise PolicyError("max_attempts must be an integer from 1 to 3")
    deps = task.get("depends_on", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise PolicyError("depends_on must be a list of task IDs")
    if "apply_local_plan" in actions:
        local_plan = task.get("local_change_plan")
        if not isinstance(local_plan, dict):
            raise PolicyError("apply_local_plan requires a local_change_plan object")
        if local_plan.get("schema_version") != 1:
            raise PolicyError("local_change_plan schema_version must be 1")
        if local_plan.get("task_id") != task["id"]:
            raise PolicyError("local_change_plan task_id must match the queue task id")
    if "run_project_check" in actions:
        if task.get("allow_project_code_execution") is not True:
            raise PolicyError("run_project_check requires allow_project_code_execution=true")
        check_id = task.get("project_check_id")
        if not isinstance(check_id, str) or not check_id.strip():
            raise PolicyError("run_project_check requires a non-empty project_check_id")
    verification_checks = task.get("verification_checks")
    if verification_checks is not None:
        allowed_check_types = {
            "file_exists", "file_sha256", "python_syntax", "json_valid",
            "tests_pass", "project_check_pass", "local_plan_applied", "report_exists",
        }
        if not isinstance(verification_checks, list) or not 1 <= len(verification_checks) <= 20:
            raise PolicyError("verification_checks must contain 1–20 check objects")
        for check in verification_checks:
            if not isinstance(check, dict) or not isinstance(check.get("type"), str):
                raise PolicyError("Every verification check must be an object with a type")
            check_type = check["type"]
            if check_type not in allowed_check_types:
                raise PolicyError(f"Unknown verification check type: {check_type}")
            allowed_keys = {"type"}
            if check_type in {"file_exists", "json_valid"}:
                allowed_keys.add("path")
            elif check_type == "file_sha256":
                allowed_keys.update({"path", "sha256"})
            if set(check) != allowed_keys:
                raise PolicyError(f"Invalid fields for verification check {check_type}")
            if "path" in check:
                path = validate_relative_path(check["path"])
                if path in {"", "."} or path != check["path"]:
                    raise PolicyError("Verification paths must be canonical file paths")
            if check_type == "file_sha256":
                import re
                if not isinstance(check["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", check["sha256"]):
                    raise PolicyError("file_sha256 requires a lowercase SHA-256 digest")
    if "apply_local_plan" in actions:
        types = {item.get("type") for item in (verification_checks or []) if isinstance(item, dict)}
        if "local_plan_applied" not in types:
            raise PolicyError("apply_local_plan requires a local_plan_applied verification check")
        if not types.intersection({"file_exists", "file_sha256", "python_syntax", "json_valid"}):
            raise PolicyError("apply_local_plan requires an artifact or syntax verification check")
    if "run_project_check" in actions:
        types = {item.get("type") for item in (verification_checks or []) if isinstance(item, dict)}
        if not types.intersection({"project_check_pass", "tests_pass"}):
            raise PolicyError("run_project_check requires a project_check_pass or tests_pass verification check")


def validate_relative_path(path: str) -> str:
    """Reject absolute paths, parent traversal and empty paths."""
    if not isinstance(path, str) or not path.strip():
        raise PolicyError("Path must be a non-empty string")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise PolicyError("Path must be repository-relative and cannot traverse directories")
    return candidate.as_posix()
