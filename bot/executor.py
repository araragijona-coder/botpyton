"""Allowlisted deterministic task executor. Task text is never executed as a command."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diagnostics import diagnose_failure
from .policy import PolicyError, validate_task
from .verifier import verify_python_files

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}
MAX_REPORTS = 16


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActionFailure(RuntimeError):
    def __init__(self, message: str, *, log_text: str = ""):
        super().__init__(message)
        self.log_text = log_text or message


class TaskExecutor:
    """Execute only fixed, source-controlled operations in a repository root."""

    def __init__(self, root: str | Path, *, test_timeout_seconds: int = 120):
        self.root = Path(root).resolve()
        self.test_timeout_seconds = max(1, min(int(test_timeout_seconds), 300))
        if not self.root.is_dir():
            raise ValueError(f"Repository root is not a directory: {self.root}")

    def execute(self, queue_record: dict[str, Any]) -> dict[str, Any]:
        task = queue_record.get("payload", queue_record)
        validate_task(task)
        action_results: list[dict[str, Any]] = []
        diagnostic = None
        failed_action = None
        error = None

        for action in task["allowed_actions"]:
            try:
                details = self._dispatch(action, task, queue_record)
                action_results.append({"action": action, "ok": True, "details": details})
            except ActionFailure as exc:
                failed_action = action
                error = str(exc)
                diagnostic = diagnose_failure(
                    exc.log_text, self.root,
                    attempt=queue_record.get("attempts", 1),
                    max_attempts=task.get("max_attempts", 3),
                )
                action_results.append({
                    "action": action, "ok": False, "error": error, "diagnostic": diagnostic
                })
                break
            except (OSError, ValueError, PolicyError) as exc:
                failed_action = action
                error = f"{type(exc).__name__}: {exc}"
                diagnostic = diagnose_failure(
                    error, self.root,
                    attempt=queue_record.get("attempts", 1),
                    max_attempts=task.get("max_attempts", 3),
                )
                action_results.append({
                    "action": action, "ok": False, "error": error, "diagnostic": diagnostic
                })
                break

        successful = failed_action is None
        report = {
            "schema_version": 1,
            "task_id": task["id"],
            "goal": task["goal"],
            "status": "succeeded" if successful else ("retryable_failure" if diagnostic and diagnostic["retryable"] else "failed"),
            "attempt": queue_record.get("attempts", 1),
            "max_attempts": task.get("max_attempts", 3),
            "failed_action": failed_action,
            "error": error,
            "diagnostic": diagnostic,
            "actions": action_results,
            "created_at": _now(),
            "ai_enabled": False,
            "external_ai_apis_enabled": False,
        }
        report_path = self._write_report(report)
        report["report_path"] = report_path
        report["ok"] = successful
        return report

    def _dispatch(self, action: str, task: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        if action == "inspect_repository":
            return self._inspect_repository()
        if action == "validate_task":
            validate_task(task)
            return {"task_id": task["id"], "valid": True}
        if action == "run_tests":
            return self._run_tests()
        if action == "apply_known_fix":
            previous_error = record.get("last_error") or task.get("failure_context")
            if not previous_error:
                return {
                    "status": "no_previous_failure",
                    "automatic_source_edit_applied": False,
                    "message": "There is no recorded failure to recover from."
                }
            return diagnose_failure(
                str(previous_error), self.root,
                attempt=record.get("attempts", 1),
                max_attempts=task.get("max_attempts", 3),
            )
        if action == "prepare_report":
            # The final report is always written after all actions; this marker records intent.
            return {"status": "final_report_will_be_written", "format": "json"}
        raise PolicyError(f"Action is not implemented: {action}")

    def _inspect_repository(self) -> dict[str, Any]:
        files: list[str] = []
        top_level: set[str] = set()
        suffix_counts: dict[str, int] = {}
        for directory, names, filenames in os.walk(self.root, topdown=True, followlinks=False):
            names[:] = sorted(name for name in names if name not in _SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                try:
                    relative = path.resolve().relative_to(self.root).as_posix()
                except ValueError:
                    continue
                files.append(relative)
                top_level.add(relative.split("/", 1)[0])
                suffix = path.suffix.lower() or "[no extension]"
                suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
                if len(files) >= 10000:
                    break
            if len(files) >= 10000:
                break
        return {
            "root": str(self.root),
            "files_count": len(files),
            "truncated": len(files) >= 10000,
            "top_level_entries": sorted(top_level),
            "file_type_counts": dict(sorted(suffix_counts.items())),
            "python_syntax_errors": verify_python_files(self.root),
        }

    def _run_tests(self) -> dict[str, Any]:
        # Fixed argv only: task fields never enter the command or environment.
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        try:
            completed = subprocess.run(
                argv, cwd=self.root, shell=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.test_timeout_seconds,
                check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(str(part) for part in (exc.stdout, exc.stderr) if part)
            raise ActionFailure(
                f"Fixed unit-test command timed out after {self.test_timeout_seconds}s",
                log_text=output + "\nTimeoutExpired",
            ) from exc
        output = (completed.stdout + "\n" + completed.stderr).strip()
        result = {
            "command_id": "python_unittest_discovery",
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout[-12000:],
            "stderr_tail": completed.stderr[-12000:],
            "timed_out": False,
        }
        if completed.returncode != 0:
            raise ActionFailure(
                f"Fixed unit-test command failed with exit code {completed.returncode}",
                log_text=output,
            )
        return result

    def _write_report(self, report: dict[str, Any]) -> str:
        report_dir = (self.root / ".cerebro" / "reports").resolve()
        report_dir.relative_to(self.root)
        report_dir.mkdir(parents=True, exist_ok=True)
        # Hash the task ID to prevent separators or traversal in user-provided IDs.
        import hashlib
        safe_id = hashlib.sha256(str(report["task_id"]).encode("utf-8")).hexdigest()[:16]
        path = report_dir / f"{safe_id}.json"
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path.relative_to(self.root).as_posix()
