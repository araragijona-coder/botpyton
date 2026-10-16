"""Safety policy for task records and deterministic operations."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

ALLOWED_ACTIONS = frozenset({
    "inspect_repository",
    "validate_task",
    "run_tests",
    "apply_known_fix",
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


def validate_relative_path(path: str) -> str:
    """Reject absolute paths, parent traversal and empty paths."""
    if not isinstance(path, str) or not path.strip():
        raise PolicyError("Path must be a non-empty string")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise PolicyError("Path must be repository-relative and cannot traverse directories")
    return candidate.as_posix()
