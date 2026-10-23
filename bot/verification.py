"""Machine-verifiable success checks for deterministic worker tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .application_cache import _is_link_or_reparse
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .policy import PolicyError, validate_relative_path
from .verifier import verify_python_files

MAX_JSON_CHECK_BYTES = 200_000


class VerificationError(ValueError):
    """Raised when a success check cannot be evaluated safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target(root: Path, raw_path: str) -> Path:
    relative = validate_relative_path(raw_path)
    if relative != raw_path or relative in {"", "."}:
        raise VerificationError(f"Verification path is not canonical: {raw_path!r}")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        # Missing path components must remain eligible for ordinary "not found"
        # results; existing components are inspected without following links.
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise VerificationError(
                f"Could not safely inspect verification path: {relative}"
            ) from exc
        if _is_link_or_reparse(current):
            raise VerificationError(
                f"Symlink or reparse-point paths are not allowed in verification checks: {relative}"
            )
    resolved = current.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VerificationError(f"Verification path escapes project root: {relative}") from exc
    return resolved


def evaluate_verification_checks(
    checks: list[dict[str, Any]] | None,
    root: str | Path,
    *,
    action_results: list[dict[str, Any]],
    report_path: str | None = None,
) -> dict:
    """Evaluate only known static/artifact checks; never execute project code."""
    if checks is None:
        return {
            "status": "not_configured",
            "checks_configured": 0,
            "all_passed": None,
            "checks": [],
            "code_executed": False,
        }
    base = Path(root).resolve()
    results = []
    for check in checks:
        check_type = check.get("type")
        result = {"type": check_type, "ok": False}
        try:
            if check_type == "file_exists":
                path = _target(base, check["path"])
                ok = path.is_file()
                result.update({"path": check["path"], "ok": ok,
                               "reason": "file exists" if ok else "file does not exist"})
            elif check_type == "file_sha256":
                path = _target(base, check["path"])
                if not path.is_file():
                    result.update({"path": check["path"], "ok": False, "reason": "file does not exist"})
                else:
                    actual = _sha256_file(path)
                    expected = check["sha256"]
                    result.update({"path": check["path"], "ok": actual == expected,
                                   "expected_sha256": expected, "actual_sha256": actual})
            elif check_type == "python_syntax":
                errors = verify_python_files(base)
                result.update({"ok": not errors, "python_syntax_errors": errors})
            elif check_type == "json_valid":
                path = _target(base, check["path"])
                if not path.is_file():
                    result.update({"path": check["path"], "ok": False,
                                   "reason": "JSON file does not exist"})
                else:
                    # Bound the actual read, not just a preceding stat: the file
                    # may grow or be replaced between checking its size and reading.
                    raw = read_bounded_bytes(
                        path,
                        limit_bytes=MAX_JSON_CHECK_BYTES,
                        label=f"JSON verification file {check['path']}",
                    )
                    value = json.loads(raw.decode("utf-8"))
                    enforce_json_depth(value, label=f"JSON verification file {check['path']}")
                    result.update({"path": check["path"], "ok": True,
                                   "reason": "valid JSON within byte and nesting limits"})
            elif check_type == "tests_pass":
                candidates = [
                    item for item in action_results
                    if item.get("action") in {"run_tests", "run_project_check"} and item.get("ok")
                ]
                result.update({"ok": bool(candidates),
                               "reason": "a test action passed" if candidates else "no successful test action"})
            elif check_type == "project_check_pass":
                candidates = [
                    item for item in action_results
                    if item.get("action") == "run_project_check"
                    and item.get("ok") and isinstance(item.get("details"), dict)
                    and item["details"].get("ok") is True
                ]
                result.update({"ok": bool(candidates),
                               "reason": "a project check passed" if candidates else "no successful project check"})
            elif check_type == "local_plan_applied":
                candidates = [
                    item for item in action_results
                    if item.get("action") == "apply_local_plan"
                    and item.get("ok") and isinstance(item.get("details"), dict)
                    and item["details"].get("writes_performed") is True
                ]
                result.update({"ok": bool(candidates),
                               "reason": "local plan applied" if candidates else "no local plan was applied"})
            elif check_type == "patch_applied":
                candidates = [
                    item for item in action_results
                    if item.get("action") == "apply_reviewed_patch"
                    and item.get("ok") and isinstance(item.get("details"), dict)
                    and item["details"].get("patch_applied") is True
                ]
                result.update({"ok": bool(candidates),
                               "reason": "reviewed patch applied in isolated worktree" if candidates else "no reviewed patch was applied"})
            elif check_type == "report_exists":
                path = _target(base, report_path) if report_path else None
                ok = bool(path and path.is_file())
                result.update({"path": report_path, "ok": ok,
                               "reason": "report exists" if ok else "report is not present"})
            else:
                raise VerificationError(f"Unknown verification check type: {check_type}")
        except (
            OSError, UnicodeError, json.JSONDecodeError, KeyError, PolicyError,
            BoundedReadError, VerificationError, RecursionError,
        ) as exc:
            result.update({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        results.append(result)
    all_passed = all(item["ok"] for item in results)
    return {
        "status": "passed" if all_passed else "failed",
        "checks_configured": len(results),
        "all_passed": all_passed,
        "checks": results,
        "code_executed": False,
    }
