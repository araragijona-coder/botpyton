"""Bounded autonomous test/repair/verification cycles without AI or network access."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diagnostics import diagnose_failure
from .process_runner import ProcessRunnerError, run_process
from .fixes import apply_known_fix
from .verifier import verify_python_files

MAX_CYCLE_ATTEMPTS = 3
MAX_TEST_TIMEOUT_SECONDS = 120
MAX_FAILURE_LOG_BYTES = 50_000
REPORT_TAIL_CHARS = 8_000
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}


class AutonomousCycleError(ValueError):
    """Raised when the cycle request violates its hard limits."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _tail_file(stream, limit: int = MAX_FAILURE_LOG_BYTES) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - limit), os.SEEK_SET)
    return stream.read(limit).decode("utf-8", errors="replace")


def _run_fixed_tests(root: Path, timeout_seconds: int) -> dict[str, Any]:
    """Run the fixed test command through the bounded process runner."""
    argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    try:
        completed = run_process(
            argv,
            cwd=root,
            allowed_root=root,
            approved_executables=[sys.executable],
            timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_FAILURE_LOG_BYTES // 2,
        )
    except ProcessRunnerError as exc:
        return {
            "command_id": "python_unittest_discovery",
            "argv": argv,
            "exit_code": None,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
            "stdout_tail": "",
            "stderr_tail": "",
            "failure_log_tail": f"ProcessRunnerError: {exc}"[:MAX_FAILURE_LOG_BYTES],
            "runner_error": type(exc).__name__,
            "process_memory_limit_enforced": False,
        }
    stdout_tail = completed["stdout"][-REPORT_TAIL_CHARS:]
    stderr_tail = completed["stderr"][-REPORT_TAIL_CHARS:]
    combined = (completed["stdout"] + "\\n" + completed["stderr"])[-MAX_FAILURE_LOG_BYTES:]
    return {
        "command_id": "python_unittest_discovery",
        "argv": argv,
        "exit_code": completed["exit_code"],
        "timed_out": completed["timed_out"],
        "timeout_seconds": timeout_seconds,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "failure_log_tail": combined,
        "stdout_truncated": completed["stdout_truncated"],
        "stderr_truncated": completed["stderr_truncated"],
        "process_memory_limit_mb": completed["process_memory_limit_mb"],
        "process_memory_limit_enforced": completed["process_memory_limit_enforced"],
    }


def _verify_repository(root: Path) -> dict[str, Any]:
    """Verify Python syntax and every repository JSON file without executing source."""
    python_errors = verify_python_files(root)
    invalid_json: list[dict[str, str]] = []
    checked_json = 0
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names[:] = sorted(name for name in names if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink() or path.suffix.lower() != ".json":
                continue
            try:
                relative = path.resolve().relative_to(root).as_posix()
                raw = path.read_text(encoding="utf-8")
                checked_json += 1
                json.loads(raw)
            except (OSError, UnicodeError, ValueError) as exc:
                invalid_json.append({"path": str(path), "error": str(exc)})
    return {
        "ok": not python_errors and not invalid_json,
        "python_files_with_syntax_errors": python_errors,
        "json_files_checked": checked_json,
        "invalid_json": invalid_json,
    }


def _repair_signature(verification: dict[str, Any]) -> str:
    python_errors = verification.get("python_files_with_syntax_errors", [])
    json_errors = verification.get("invalid_json", [])
    if python_errors and not json_errors:
        details = "\n".join(python_errors)
        if "expected ':'" in details:
            return "SyntaxError: expected ':'\n" + details
        return "SyntaxError: static repository verification failed\n" + details
    if json_errors and not python_errors:
        details = "\n".join(f"{item['path']}: {item['error']}" for item in json_errors)
        return "JSONDecodeError: static repository verification failed\n" + details
    if python_errors or json_errors:
        return "SyntaxError JSONDecodeError: mixed static verification failures"
    return ""


def run_autonomous_cycle(
    root: str | Path,
    *,
    max_attempts: int = 3,
    test_timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Run a hard-bounded test -> exact repair -> rerun -> verify cycle.

    At most one known-pattern repair is attempted after any failed verification.
    Unknown, ambiguous, timed-out, or final-attempt failures stop without edits.
    Every attempt is written to a separate JSON record before the next attempt.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_CYCLE_ATTEMPTS:
        raise AutonomousCycleError(f"max_attempts must be an integer from 1 to {MAX_CYCLE_ATTEMPTS}")
    if type(test_timeout_seconds) is not int or not 1 <= test_timeout_seconds <= MAX_TEST_TIMEOUT_SECONDS:
        raise AutonomousCycleError(
            f"test_timeout_seconds must be an integer from 1 to {MAX_TEST_TIMEOUT_SECONDS}"
        )
    base = Path(root).resolve()
    if not base.is_dir():
        raise AutonomousCycleError("Repository root must be an existing directory")

    reports_root = base / ".cerebro" / "reports" / "autonomous-cycles"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    cycle_id = f"{stamp}-{os.getpid()}"
    report_dir = reports_root / cycle_id
    report_dir.mkdir(parents=True, exist_ok=False)

    summary: dict[str, Any] = {
        "schema_version": 1,
        "cycle_id": cycle_id,
        "status": "running",
        "max_attempts": max_attempts,
        "test_timeout_seconds": test_timeout_seconds,
        "attempts_completed": 0,
        "attempt_records": [],
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
        "automatic_merge": False,
        "started_at": _now(),
        "finished_at": None,
    }
    summary_path = report_dir / "summary.json"
    _write_json_atomic(summary_path, summary)

    for attempt_number in range(1, max_attempts + 1):
        attempt_started = _now()
        attempt_path = report_dir / f"attempt-{attempt_number:03d}.json"
        attempt_record: dict[str, Any] = {
            "schema_version": 1,
            "cycle_id": cycle_id,
            "attempt": attempt_number,
            "max_attempts": max_attempts,
            "started_at": attempt_started,
            "test_result": None,
            "verification": None,
            "diagnostic": None,
            "repair": {
                "attempted": False,
                "applied": False,
                "reason": "not_started",
                "automatic_source_edit_applied": False,
            },
            "status": "running",
            "finished_at": None,
            "ai_enabled": False,
            "external_ai_apis_enabled": False,
        }
        # Persist the attempt before launching the test process, so interruptions leave evidence.
        _write_json_atomic(attempt_path, attempt_record)
        summary["attempts_started"] = attempt_number
        summary["attempt_records"].append(attempt_path.name)
        _write_json_atomic(summary_path, summary)

        test_result = _run_fixed_tests(base, test_timeout_seconds)
        attempt_record["test_result"] = {
            key: value for key, value in test_result.items()
            if key != "failure_log_tail"
        }
        # Persist test results before any repair operation.
        _write_json_atomic(attempt_path, attempt_record)
        failure_log = test_result["failure_log_tail"]
        test_ok = test_result["exit_code"] == 0 and not test_result["timed_out"]
        if test_ok:
            verification = _verify_repository(base)
            attempt_record["verification"] = verification
            if verification["ok"]:
                attempt_record["status"] = "succeeded"
                attempt_record["repair"]["reason"] = "tests_and_static_verification_passed"
                attempt_record["finished_at"] = _now()
                _write_json_atomic(attempt_path, attempt_record)
                summary["attempts_completed"] = attempt_number
                summary["status"] = "succeeded"
                summary["finished_at"] = _now()
                summary["final_verification"] = verification
                _write_json_atomic(summary_path, summary)
                return {**summary, "report_path": summary_path.relative_to(base).as_posix()}

            failure_log = _repair_signature(verification)
            attempt_record["diagnostic"] = diagnose_failure(
                failure_log, base, attempt=attempt_number, max_attempts=max_attempts
            )
        else:
            attempt_record["verification"] = {
                "ok": False,
                "reason": "test_timeout" if test_result["timed_out"] else "unit_tests_failed",
            }
            if not test_result["timed_out"]:
                attempt_record["diagnostic"] = diagnose_failure(
                    failure_log, base, attempt=attempt_number, max_attempts=max_attempts
                )

        if attempt_number >= max_attempts:
            attempt_record["repair"].update({
                "attempted": False,
                "applied": False,
                "reason": "attempt_limit_reached; no unverified final repair will be made",
            })
            attempt_record["status"] = "failed"
        elif test_result["timed_out"]:
            attempt_record["repair"]["reason"] = "test_timeout; automatic repair disabled after timeout"
            attempt_record["status"] = "failed"
        else:
            attempt_record["repair"]["attempted"] = True
            fix_result = apply_known_fix(failure_log, base)
            attempt_record["repair"].update(fix_result)
            attempt_record["repair"]["attempted"] = True
            if fix_result.get("applied"):
                post_repair_verification = _verify_repository(base)
                attempt_record["repair"]["post_repair_verification"] = post_repair_verification
                attempt_record["repair"]["post_repair_verification_passed"] = bool(
                    post_repair_verification["ok"]
                )
                if post_repair_verification["ok"]:
                    attempt_record["status"] = "repaired_rerun_required"
                else:
                    # The source edit did occur; preserve that fact even though verification failed.
                    attempt_record["repair"]["automatic_source_edit_applied"] = True
                    attempt_record["repair"]["reason"] = (
                        "repair applied but repository-wide verification did not pass; cycle stopped"
                    )
                    attempt_record["status"] = "failed"
            else:
                attempt_record["status"] = "failed"

        attempt_record["finished_at"] = _now()
        _write_json_atomic(attempt_path, attempt_record)
        summary["attempts_completed"] = attempt_number

        if attempt_record["status"] != "repaired_rerun_required":
            summary["status"] = "failed"
            summary["finished_at"] = _now()
            summary["last_failure"] = attempt_record.get("diagnostic") or attempt_record["verification"]
            _write_json_atomic(summary_path, summary)
            return {**summary, "report_path": summary_path.relative_to(base).as_posix()}

        summary["status"] = "running"
        summary["last_repair"] = attempt_record["repair"]
        _write_json_atomic(summary_path, summary)

    summary["status"] = "failed"
    summary["finished_at"] = _now()
    _write_json_atomic(summary_path, summary)
    return {**summary, "report_path": summary_path.relative_to(base).as_posix()}
