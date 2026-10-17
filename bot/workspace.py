"""Git worktree isolation for clean repositories; no merges or network operations."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .process_runner import ProcessRunnerError, run_process

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class WorkspaceError(ValueError):
    """Raised when a safe isolated workspace cannot be prepared or inspected."""


def _git(root: Path, args: list[str], *, timeout_seconds: int = 20) -> dict:
    executable = shutil.which("git")
    if not executable:
        raise WorkspaceError("Git is not installed or not available on PATH")
    try:
        result = run_process(
            [executable, *args],
            cwd=root,
            allowed_root=root,
            approved_executables=[executable],
            timeout_seconds=timeout_seconds,
            output_limit_bytes=64_000,
        )
    except ProcessRunnerError as exc:
        raise WorkspaceError(f"Git operation could not run: {exc}") from exc
    if result["timed_out"]:
        raise WorkspaceError("Git operation timed out")
    return result


def create_isolated_workspace(
    project_root: str | Path,
    task_id: str,
    *,
    workspace_parent: str | Path | None = None,
) -> dict:
    """Create a dedicated branch/worktree only when the source repository is clean."""
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise WorkspaceError("task_id must contain 1–64 safe ASCII letters, digits, '.', '_' or '-'")
    requested_root = Path(project_root).expanduser().resolve()
    if not requested_root.is_dir():
        raise WorkspaceError("Project root must be an existing directory")

    top = _git(requested_root, ["rev-parse", "--show-toplevel"])
    if top["exit_code"] != 0:
        raise WorkspaceError("Selected project is not inside a Git repository")
    git_root = Path(top["stdout"].strip()).resolve()
    if git_root != requested_root:
        raise WorkspaceError(f"Select the repository root, not a subdirectory: {git_root}")

    head = _git(git_root, ["rev-parse", "HEAD"])
    if head["exit_code"] != 0:
        raise WorkspaceError("Could not resolve the current Git HEAD; commit the initial project first")
    base_sha = head["stdout"].strip()
    status = _git(git_root, ["status", "--porcelain", "--untracked-files=all"])
    if status["exit_code"] != 0:
        raise WorkspaceError("Could not inspect the source repository status")
    if status["stdout"].strip() or status["stdout_truncated"]:
        raise WorkspaceError("Source repository is not clean; commit or stash changes before creating a worktree")

    parent = Path(workspace_parent).expanduser().resolve() if workspace_parent else (
        Path.home() / ".botpyton" / "workspaces"
    ).resolve()
    try:
        parent.relative_to(git_root)
        parent_inside_repo = True
    except ValueError:
        parent_inside_repo = False
    if parent_inside_repo:
        raise WorkspaceError("Workspace parent must be outside the source repository")
    parent.mkdir(parents=True, exist_ok=True)

    fingerprint = hashlib.sha256(f"{git_root}\n{base_sha}\n{task_id}".encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:32] or "task"
    branch = f"botpyton/{slug}-{fingerprint}"
    workspace = parent / f"{slug}-{fingerprint}"
    if workspace.exists():
        raise WorkspaceError(f"Workspace path already exists; refusing to overwrite: {workspace}")

    result = _git(git_root, ["worktree", "add", "-b", branch, str(workspace), base_sha], timeout_seconds=60)
    if result["exit_code"] != 0:
        reason = (result["stderr"] or result["stdout"]).strip()[-2000:]
        raise WorkspaceError(f"Git could not create the worktree: {reason}")
    return {
        "status": "created",
        "source_root": str(git_root),
        "workspace_path": str(workspace),
        "branch": branch,
        "base_sha": base_sha,
        "task_id": task_id,
        "merge_performed": False,
        "push_performed": False,
        "network_used": False,
        "ai_enabled": False,
    }


def inspect_workspace(workspace_path: str | Path) -> dict:
    """Read status and diff summary for an existing Git worktree; do not modify it."""
    root = Path(workspace_path).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceError("Workspace path must be an existing directory")
    top = _git(root, ["rev-parse", "--show-toplevel"])
    if top["exit_code"] != 0 or Path(top["stdout"].strip()).resolve() != root:
        raise WorkspaceError("Selected path is not the root of a Git worktree")
    head = _git(root, ["rev-parse", "HEAD"])
    status = _git(root, ["status", "--short", "--untracked-files=all"])
    diff = _git(root, ["diff", "--stat", "HEAD"])
    if any(item["exit_code"] != 0 for item in (head, status, diff)):
        raise WorkspaceError("Could not inspect worktree state")
    return {
        "status": "inspection_complete",
        "workspace_path": str(root),
        "head_sha": head["stdout"].strip(),
        "working_tree_status": status["stdout"],
        "diff_stat": diff["stdout"],
        "has_changes": bool(status["stdout"].strip()),
        "merge_performed": False,
        "push_performed": False,
        "network_used": False,
        "ai_enabled": False,
    }
