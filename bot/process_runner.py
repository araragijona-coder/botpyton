"""Bounded subprocess execution with explicit executable approval and no shell."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from .tool_registry import TOOL_SPECS

MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_BYTES = 64_000
SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE_KEY")


class ProcessRunnerError(ValueError):
    """Raised when a process request violates execution policy."""


def _safe_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_MARKERS)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProcessRunnerError("Environment overrides must be string pairs")
            if not key or any(ch in key for ch in "=\0"):
                raise ProcessRunnerError("Invalid environment variable name")
            if any(marker in key.upper() for marker in SECRET_MARKERS):
                raise ProcessRunnerError("Secret-like environment overrides are forbidden")
            if "\0" in value:
                raise ProcessRunnerError("Environment values cannot contain NUL")
            env[key] = value
    return env


def _resolve_approved_executable(executable: str, approved_executables: Sequence[str]) -> str:
    if not isinstance(executable, str) or not executable or "\0" in executable:
        raise ProcessRunnerError("Executable must be a non-empty string")
    resolved = shutil.which(executable)
    if resolved is None and Path(executable).is_absolute() and Path(executable).is_file():
        resolved = str(Path(executable).resolve())
    if resolved is None:
        raise ProcessRunnerError(f"Executable is not available: {executable}")
    resolved_path = str(Path(resolved).resolve())
    approved = set()
    for candidate in approved_executables:
        candidate_resolved = shutil.which(candidate)
        if candidate_resolved is None and Path(candidate).is_absolute() and Path(candidate).is_file():
            candidate_resolved = str(Path(candidate).resolve())
        if candidate_resolved:
            approved.add(str(Path(candidate_resolved).resolve()).casefold())
    if resolved_path.casefold() not in approved:
        raise ProcessRunnerError("Executable is not in the caller's explicit allowlist")
    return resolved_path


def run_process(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    allowed_root: str | Path,
    approved_executables: Sequence[str],
    timeout_seconds: int = 30,
    output_limit_bytes: int = MAX_OUTPUT_BYTES,
    env_overrides: Mapping[str, str] | None = None,
) -> dict:
    """Run an explicitly approved executable with bounded time and returned output.

    Callers must supply an allowlist; arguments are passed as an array and shell=False.
    Output is spooled to temporary files so large logs do not have to reside in RAM.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ProcessRunnerError("argv must be a non-empty sequence of separate arguments")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ProcessRunnerError(f"timeout_seconds must be from 1 to {MAX_TIMEOUT_SECONDS}")
    if not isinstance(output_limit_bytes, int) or isinstance(output_limit_bytes, bool) or not 1 <= output_limit_bytes <= MAX_OUTPUT_BYTES:
        raise ProcessRunnerError(f"output_limit_bytes must be from 1 to {MAX_OUTPUT_BYTES}")
    normalized_argv = []
    for arg in argv:
        if not isinstance(arg, str) or "\0" in arg:
            raise ProcessRunnerError("Every process argument must be a NUL-free string")
        normalized_argv.append(arg)
    if not normalized_argv[0]:
        raise ProcessRunnerError("Executable argument cannot be empty")

    root = Path(allowed_root).expanduser().resolve()
    workdir = Path(cwd).expanduser().resolve()
    if not root.is_dir() or not workdir.is_dir():
        raise ProcessRunnerError("allowed_root and cwd must be existing directories")
    try:
        workdir.relative_to(root)
    except ValueError as exc:
        raise ProcessRunnerError("cwd must be inside allowed_root") from exc
    executable = _resolve_approved_executable(normalized_argv[0], approved_executables)
    normalized_argv[0] = executable
    child_env = _safe_environment(env_overrides)

    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        try:
            completed = subprocess.run(
                normalized_argv,
                cwd=workdir,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                timeout=timeout_seconds,
                check=False,
            )
            timed_out = False
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_raw = stdout_file.read(output_limit_bytes + 1)
        stderr_raw = stderr_file.read(output_limit_bytes + 1)
    stdout_truncated = len(stdout_raw) > output_limit_bytes
    stderr_truncated = len(stderr_raw) > output_limit_bytes
    return {
        "argv": normalized_argv,
        "cwd": str(workdir),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "stdout": stdout_raw[:output_limit_bytes].decode("utf-8", errors="replace"),
        "stderr": stderr_raw[:output_limit_bytes].decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "shell_used": False,
        "stdin_inherited": False,
        "secret_environment_variables_forwarded": False,
    }


def run_tool_probe(
    tool_id: str,
    *,
    root: str | Path,
    timeout_seconds: int = 10,
) -> dict:
    """Run only a fixed version probe for a tool already registered in source."""
    spec = next((item for item in TOOL_SPECS if item.tool_id == tool_id), None)
    if spec is None:
        raise ProcessRunnerError(f"Unknown registered tool id: {tool_id}")
    executable_path = shutil.which(spec.executable)
    if executable_path is None:
        raise ProcessRunnerError(f"Registered tool is not installed: {tool_id}")
    result = run_process(
        [executable_path, *spec.probe_args],
        cwd=root,
        allowed_root=root,
        approved_executables=[executable_path],
        timeout_seconds=timeout_seconds,
    )
    result["tool_id"] = tool_id
    result["purpose"] = spec.purpose
    return result
