"""Apply an explicitly reviewed unified diff in an isolated Git worktree.

The source repository is never edited, and project checks run only after explicit
approval. The candidate is left on a separate local branch for manual review; this
module never commits, pushes, merges, installs dependencies, or retries a failed check.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .bounded_io import BoundedReadError, read_bounded_bytes
from .diagnostics import diagnose_failure
from .local_changes import (
    MAX_FILE_BYTES,
    LocalChangeError,
    _reject_link_components,
    _resolve_target,
    _safe_relative_path,
)
from .project_checks import ProjectCheckError, discover_project_checks, run_project_check
from .workspace import WorkspaceError, _git, create_isolated_workspace, inspect_workspace

MAX_PATCH_BYTES = 250_000
MAX_PATCH_FILES = 20
MAX_PATCH_LINES = 20_000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
_HEADER_PREFIX = "diff --git "


class PatchCycleError(ValueError):
    """Raised when a reviewed patch request is malformed or unsafe."""


def _header_path(line: str, prefix: str) -> str:
    try:
        parts = shlex.split(line[len(prefix):], posix=True)
    except ValueError as exc:
        raise PatchCycleError(f"Malformed patch header: {line[:160]!r}") from exc
    if len(parts) != 1:
        raise PatchCycleError(f"Patch header must name exactly one path: {line[:160]!r}")
    return parts[0]


def _diff_paths(patch: str) -> list[str]:
    """Parse and constrain the file-level shape before handing a diff to Git."""
    lines = patch.splitlines()
    if len(lines) > MAX_PATCH_LINES:
        raise PatchCycleError(f"Patch exceeds {MAX_PATCH_LINES} lines")
    if not lines or not lines[0].startswith(_HEADER_PREFIX):
        raise PatchCycleError("Input must be a plain unified Git diff beginning with 'diff --git'; omit Markdown fences.")
    starts = [index for index, line in enumerate(lines) if line.startswith(_HEADER_PREFIX)]
    if not starts or len(starts) > MAX_PATCH_FILES:
        raise PatchCycleError(f"Patch must modify between 1 and {MAX_PATCH_FILES} files")
    starts.append(len(lines))
    paths: list[str] = []
    for position in range(len(starts) - 1):
        section = lines[starts[position]:starts[position + 1]]
        header = section[0]
        try:
            pair = shlex.split(header[len(_HEADER_PREFIX):], posix=True)
        except ValueError as exc:
            raise PatchCycleError("Malformed diff --git path header") from exc
        if len(pair) != 2 or not pair[0].startswith("a/") or not pair[1].startswith("b/"):
            raise PatchCycleError("Only ordinary a/… to b/… Git diff paths are supported")
        old_path, new_path = pair[0][2:], pair[1][2:]
        if old_path != new_path:
            raise PatchCycleError("Renames and cross-path changes are not supported by the reviewed patch cycle")
        if any(line.startswith((
            "new file mode ", "deleted file mode ", "old mode ", "new mode ",
            "rename from ", "rename to ", "copy from ", "copy to ",
            "GIT binary patch", "Binary files ",
        )) for line in section):
            raise PatchCycleError("File creation/deletion, rename/copy, mode changes and binary patches are blocked")
        if any(re.search(r"\b120000\b", line) for line in section if line.startswith("index ")):
            raise PatchCycleError("Symbolic-link patches are not supported")
        old_headers = [line for line in section if line.startswith("--- ")]
        new_headers = [line for line in section if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise PatchCycleError("Every diff section must contain one --- and one +++ file header")
        old_header_path = _header_path(old_headers[0], "--- ")
        new_header_path = _header_path(new_headers[0], "+++ ")
        if old_header_path != f"a/{old_path}" or new_header_path != f"b/{new_path}":
            raise PatchCycleError("Diff file headers do not match the diff --git path")
        if not any(line.startswith("@@ ") or line.startswith("@@") for line in section):
            raise PatchCycleError(f"Diff has no hunks for {old_path}")
        try:
            safe_path = _safe_relative_path(old_path)
        except LocalChangeError as exc:
            raise PatchCycleError(str(exc)) from exc
        if safe_path != old_path:
            raise PatchCycleError(f"Patch path is not canonical: {old_path!r}")
        if old_path in paths:
            raise PatchCycleError(f"Patch contains more than one section for {old_path}")
        paths.append(old_path)
    return paths


def _check_existing_targets(root: Path, paths: list[str]) -> None:
    for relative in paths:
        try:
            target = _resolve_target(root, relative)
        except LocalChangeError as exc:
            raise PatchCycleError(str(exc)) from exc
        try:
            info = target.stat(follow_symlinks=False)
        except OSError as exc:
            raise PatchCycleError(f"Cannot inspect patch target {relative!r}") from exc
        if not stat.S_ISREG(info.st_mode) or target.is_symlink():
            raise PatchCycleError(f"Patch target must be an existing regular file: {relative}")
        if info.st_size < 0 or info.st_size > MAX_FILE_BYTES:
            raise PatchCycleError(f"Existing patch target exceeds {MAX_FILE_BYTES} bytes: {relative}")


def _git_output(root: Path, args: list[str]) -> str:
    result = _git(root, args, timeout_seconds=45)
    if result["exit_code"] != 0:
        reason = (result.get("stderr") or result.get("stdout") or "Git operation failed").strip()
        raise PatchCycleError(reason[-2_000:])
    return result.get("stdout", "")


def _verify_changed_files(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    """Run bounded, dependency-free static checks on the changed files only."""
    results: list[dict[str, Any]] = []
    for relative in paths:
        target = _resolve_target(root, relative)
        if not target.is_file() or target.is_symlink():
            results.append({"path": relative, "check": "regular_file", "ok": False, "reason": "Target is missing or linked"})
            continue
        try:
            raw = read_bounded_bytes(target, limit_bytes=MAX_FILE_BYTES, label=f"Changed file {relative}")
            suffix = target.suffix.casefold()
            if suffix in {".py", ".pyi"}:
                ast.parse(raw.decode("utf-8"), filename=relative)
                results.append({"path": relative, "check": "python_ast_parse", "ok": True})
            elif suffix == ".json":
                json.loads(raw.decode("utf-8"))
                results.append({"path": relative, "check": "json_parse", "ok": True})
            elif suffix == ".toml":
                tomllib.loads(raw.decode("utf-8"))
                results.append({"path": relative, "check": "toml_parse", "ok": True})
            else:
                results.append({
                    "path": relative, "check": "git_diff_check_only", "ok": True,
                    "note": "This file type has no dependency-free semantic validator in BOTPYTON; project-specific checks or human review are still required.",
                })
        except (BoundedReadError, UnicodeError, SyntaxError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
            results.append({
                "path": relative, "check": "static_parse", "ok": False,
                "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
    return results


def _check_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Keep CI/program output bounded while retaining useful failure evidence."""
    return {
        "check_id": result.get("check_id"),
        "description": result.get("description"),
        "ok": result.get("ok") is True,
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
        "timeout_seconds": result.get("timeout_seconds"),
        "process_memory_limit_mb": result.get("process_memory_limit_mb"),
        "process_memory_limit_enforced": result.get("process_memory_limit_enforced"),
        "shell_used": result.get("shell_used"),
        "stdout_tail": str(result.get("stdout", ""))[-8_000:],
        "stderr_tail": str(result.get("stderr", ""))[-8_000:],
    }


def apply_reviewed_patch(
    patch_path: str | Path,
    project_root: str | Path,
    task_id: str,
    *,
    workspace_parent: str | Path | None = None,
    project_check_id: str | None = None,
    execute_project_code: bool = False,
    timeout_seconds: int = 120,
    memory_limit_mb: int | None = None,
) -> dict[str, Any]:
    """Apply a supplied, reviewed unified diff on a fresh worktree; never merge/push."""
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise PatchCycleError("task_id must be 1–48 safe ASCII letters, digits, '.', '_' or '-'")
    if not isinstance(execute_project_code, bool):
        raise PatchCycleError("execute_project_code must be boolean")
    if project_check_id is not None and not execute_project_code:
        raise PatchCycleError("A project check executes code; pass the explicit --execute-project-code consent")
    if execute_project_code and not project_check_id:
        raise PatchCycleError("Project-code consent requires an explicit discovered --patch-check CHECK_ID")
    if project_check_id is not None and (not isinstance(project_check_id, str) or not project_check_id.strip()):
        raise PatchCycleError("project_check_id must be a non-empty check identifier")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 300:
        raise PatchCycleError("timeout_seconds must be from 1 to 300")
    if memory_limit_mb is not None and (
        not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool)
        or not 64 <= memory_limit_mb <= 1024
    ):
        raise PatchCycleError("memory_limit_mb must be from 64 to 1024")

    source_input = Path(project_root).expanduser()
    try:
        _reject_link_components(source_input, "Project root")
        root = source_input.resolve(strict=True)
    except (LocalChangeError, OSError, RuntimeError, ValueError) as exc:
        raise PatchCycleError(f"Project root cannot be resolved safely ({type(exc).__name__})") from None
    if not root.is_dir():
        raise PatchCycleError("Project root must be an existing directory")

    patch_input = Path(patch_path).expanduser()
    try:
        _reject_link_components(patch_input, "Patch input")
        patch_bytes = read_bounded_bytes(patch_input, limit_bytes=MAX_PATCH_BYTES, label="Reviewed patch")
        patch_text = patch_bytes.decode("utf-8")
    except (LocalChangeError, BoundedReadError, UnicodeError, OSError, RuntimeError, ValueError) as exc:
        raise PatchCycleError(f"Patch input could not be read safely ({type(exc).__name__}): {str(exc)[:400]}") from None
    if b"\x00" in patch_bytes:
        raise PatchCycleError("Binary patch data is not supported")
    paths = _diff_paths(patch_text)
    _check_existing_targets(root, paths)

    available_check = None
    if project_check_id:
        try:
            discovered = discover_project_checks(root)
        except (ProjectCheckError, OSError, RuntimeError, ValueError) as exc:
            raise PatchCycleError(f"Could not discover project checks ({type(exc).__name__})") from None
        available_check = next((item for item in discovered["checks"] if item["check_id"] == project_check_id), None)
        if available_check is None:
            raise PatchCycleError(
                f"Requested check {project_check_id!r} was not discovered in the project. "
                f"Available checks: {', '.join(item['check_id'] for item in discovered['checks']) or '(none)'}"
            )

    parent = str(workspace_parent) if workspace_parent is not None else None
    baseline: dict[str, Any] | None = None
    baseline_workspace: str | None = None
    baseline_run: dict[str, Any] | None = None
    candidate_run: dict[str, Any] | None = None
    project_code_execution_attempted = False
    project_code_executed = False

    try:
        candidate_data = create_isolated_workspace(root, f"{task_id}-candidate", workspace_parent=parent)
    except WorkspaceError as exc:
        raise PatchCycleError(f"Could not create candidate worktree ({type(exc).__name__}): {exc}") from exc
    candidate_root = Path(candidate_data["workspace_path"])

    temporary_patch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="botpyton-reviewed-", suffix=".patch", delete=False) as stream:
            temporary_patch = stream.name
            stream.write(patch_bytes)

        checked = _git(candidate_root, ["apply", "--check", "--whitespace=error", temporary_patch], timeout_seconds=45)
        if checked["exit_code"] != 0:
            reason = (checked.get("stderr") or checked.get("stdout") or "git apply --check rejected the patch").strip()
            return {
                "status": "patch_rejected",
                "task_id": task_id,
                "reason": reason[-2_000:],
                "patch": {"sha256": hashlib.sha256(patch_bytes).hexdigest(), "bytes": len(patch_bytes), "files": paths, "applied": False},
                "candidate_workspace": str(candidate_root),
                "project_code_executed": False,
                "project_code_execution_attempted": False,
                "network_used": False,
                "ai_enabled": False,
                "merge_performed": False,
                "push_performed": False,
                "human_review_required": True,
            }

        # Run the baseline only after Git confirms the diff is applicable.
        # A stale/invalid patch cannot trigger unrelated project code execution.
        if project_check_id:
            try:
                baseline_data = create_isolated_workspace(
                    root, f"{task_id}-baseline", workspace_parent=parent
                )
                baseline_workspace = baseline_data["workspace_path"]
            except WorkspaceError as exc:
                raise PatchCycleError(f"Could not create baseline worktree ({type(exc).__name__}): {exc}") from exc
            try:
                project_code_execution_attempted = True
                baseline_run = run_project_check(
                    project_check_id, baseline_workspace, execute_project_code=True,
                    timeout_seconds=timeout_seconds, memory_limit_mb=memory_limit_mb,
                )
                project_code_executed = True
                baseline = _check_summary(baseline_run)
            except (ProjectCheckError, OSError, RuntimeError, ValueError) as exc:
                baseline = {
                    "check_id": project_check_id, "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }

        applied = _git(candidate_root, ["apply", "--whitespace=error", temporary_patch], timeout_seconds=45)
        if applied["exit_code"] != 0:
            reason = (applied.get("stderr") or applied.get("stdout") or "git apply failed").strip()
            return {
                "status": "patch_apply_failed",
                "task_id": task_id,
                "reason": reason[-2_000:],
                "patch": {"sha256": hashlib.sha256(patch_bytes).hexdigest(), "bytes": len(patch_bytes), "files": paths, "applied": False},
                "candidate_workspace": str(candidate_root),
                "baseline_workspace": baseline_workspace,
                "baseline_check": baseline,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "network_used": False,
                "ai_enabled": False,
                "merge_performed": False,
                "push_performed": False,
                "human_review_required": True,
            }
    finally:
        if temporary_patch:
            try:
                Path(temporary_patch).unlink()
            except OSError:
                pass

    try:
        changed_output = _git_output(candidate_root, ["diff", "--name-only"])
        changed_paths = sorted(line for line in changed_output.splitlines() if line)
        if changed_paths != sorted(paths):
            return {
                "status": "candidate_verification_failed",
                "task_id": task_id,
                "reason": "Changed paths did not exactly match the validated patch headers.",
                "patch": {"sha256": hashlib.sha256(patch_bytes).hexdigest(), "bytes": len(patch_bytes), "files": paths, "applied": True},
                "candidate_workspace": str(candidate_root),
                "observed_changed_paths": changed_paths,
                "source_working_tree_modified": False,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "network_used": False,
                "ai_enabled": False,
                "merge_performed": False,
                "push_performed": False,
                "human_review_required": True,
            }
        diff_check = _git(candidate_root, ["diff", "--check"])
        if diff_check["exit_code"] != 0:
            reason = (diff_check.get("stderr") or diff_check.get("stdout") or "git diff --check failed").strip()
            static = [{"check": "git_diff_check", "ok": False, "reason": reason[-1_000:]}]
        else:
            static = _verify_changed_files(candidate_root, paths)
    except (PatchCycleError, WorkspaceError, LocalChangeError, OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "candidate_verification_failed",
            "task_id": task_id,
            "reason": f"{type(exc).__name__}: {str(exc)[:1_000]}",
            "patch": {"sha256": hashlib.sha256(patch_bytes).hexdigest(), "bytes": len(patch_bytes), "files": paths, "applied": True},
            "candidate_workspace": str(candidate_root),
            "source_repository_modified": False,
            "project_code_executed": bool(baseline),
            "network_used": False,
            "ai_enabled": False,
            "merge_performed": False,
            "push_performed": False,
            "human_review_required": True,
        }

    static_ok = all(item.get("ok") is True for item in static)
    if not static_ok:
        return {
            "status": "candidate_static_verification_failed",
            "task_id": task_id,
            "patch": {"sha256": hashlib.sha256(patch_bytes).hexdigest(), "bytes": len(patch_bytes), "files": paths, "applied": True},
            "candidate_workspace": str(candidate_root),
            "baseline_workspace": baseline_workspace,
            "baseline_check": baseline,
            "verification": {"changed_paths_match": True, "git_diff_check": diff_check["exit_code"] == 0, "changed_files": static},
            "project_check_run": False,
            "reason": "Static verification failed; project code was not executed on the candidate.",
            "source_repository_modified": False,
            "project_code_executed": bool(baseline),
            "network_used": False,
            "ai_enabled": False,
            "merge_performed": False,
            "push_performed": False,
            "human_review_required": True,
        }

    candidate_check = None
    diagnostic = None
    if project_check_id:
        try:
            project_code_execution_attempted = True
            candidate_run = run_project_check(
                project_check_id, candidate_root, execute_project_code=True,
                timeout_seconds=timeout_seconds, memory_limit_mb=memory_limit_mb,
            )
            project_code_executed = True
            candidate_check = _check_summary(candidate_run)
            if not candidate_run.get("ok"):
                log = (str(candidate_run.get("stdout", "")) + "\n" + str(candidate_run.get("stderr", "")))[-16_000:]
                if log.strip():
                    try:
                        diagnostic = diagnose_failure(log, candidate_root)
                    except (OSError, RuntimeError, ValueError):
                        diagnostic = {
                            "status": "diagnostic_incomplete",
                            "root_cause_confirmed": False,
                            "note": "The failure log could not be classified safely; preserve the output and request review.",
                        }
        except (ProjectCheckError, OSError, RuntimeError, ValueError) as exc:
            candidate_check = {"check_id": project_check_id, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}

    candidate_ok = bool(candidate_check and candidate_check.get("ok") is True)
    status = (
        "candidate_tests_passed" if candidate_ok else
        "candidate_checks_failed" if project_check_id else
        "candidate_ready_for_review"
    )
    try:
        workspace_inspection = inspect_workspace(candidate_root)
    except WorkspaceError as exc:
        return {
            "status": "candidate_verification_failed",
            "task_id": task_id,
            "reason": f"Could not inspect the candidate diff ({type(exc).__name__}): {str(exc)[:500]}",
            "candidate_workspace": str(candidate_root),
            "source_working_tree_modified": False,
            "project_code_executed": project_code_executed,
            "project_code_execution_attempted": project_code_execution_attempted,
            "network_used": False,
            "ai_enabled": False,
            "merge_performed": False,
            "push_performed": False,
            "human_review_required": True,
        }
    return {
        "status": status,
        "task_id": task_id,
        "base_sha": candidate_data["base_sha"],
        "candidate_branch": candidate_data["branch"],
        "candidate_workspace": str(candidate_root),
        "candidate_worktree_status": workspace_inspection["working_tree_status"],
        "candidate_diff_stat": workspace_inspection["diff_stat"],
        "baseline_workspace": baseline_workspace,
        "patch": {
            "sha256": hashlib.sha256(patch_bytes).hexdigest(),
            "bytes": len(patch_bytes),
            "files": paths,
            "applied": True,
        },
        "verification": {
            "git_apply_check": True,
            "changed_paths_match": True,
            "git_diff_check": True,
            "changed_files": static,
            "project_check_ran_on_candidate": bool(project_check_id),
        },
        "baseline_check": baseline,
        "candidate_check": candidate_check,
        "diagnostic": diagnostic,
        "decision": "review_candidate" if candidate_ok or not project_check_id else "investigate_or_request_help",
        "notes": [
            "The original source worktree was not modified.",
            "The candidate remains uncommitted in an isolated worktree; no push or merge was performed.",
            "Static parsing and passing tests are evidence, not proof of universal correctness.",
            "A failed candidate check is not retried with identical inputs; inspect the diagnostic and make a new reviewed patch.",
        ],
        "source_repository_modified": False,
        "project_code_executed": project_code_executed,
            "project_code_execution_attempted": project_code_execution_attempted,
        "network_used": False,
        "ai_enabled": False,
        "merge_performed": False,
        "push_performed": False,
        "human_review_required": True,
    }
