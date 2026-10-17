"""Bounded subprocess execution with explicit executable approval and no shell."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
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


def _bounded_reader(stream, limit: int, storage: dict, key: str) -> None:
    chunks = []
    stored = 0
    truncated = False
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            remaining = limit - stored
            if remaining > 0:
                kept = chunk[:remaining]
                chunks.append(kept)
                stored += len(kept)
            if len(chunk) > max(remaining, 0):
                truncated = True
    finally:
        storage[key] = b"".join(chunks)
        storage[key + "_truncated"] = truncated
        stream.close()


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
    """Run an approved executable; discard excess output while draining pipes."""
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
    normalized_argv[0] = _resolve_approved_executable(normalized_argv[0], approved_executables)
    child_env = _safe_environment(env_overrides)
    captured: dict = {}
    process = None
    timed_out = False
    try:
        process = subprocess.Popen(
            normalized_argv,
            cwd=workdir,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
        )
        stdout_thread = threading.Thread(
            target=_bounded_reader, args=(process.stdout, output_limit_bytes, captured, "stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_bounded_reader, args=(process.stderr, output_limit_bytes, captured, "stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise ProcessRunnerError("Output collector did not stop after process termination")
        exit_code = process.returncode
    except OSError as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise ProcessRunnerError(f"Process launch failed: {type(exc).__name__}") from exc

    return {
        "argv": normalized_argv,
        "cwd": str(workdir),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "stdout": captured.get("stdout", b"").decode("utf-8", errors="replace"),
        "stderr": captured.get("stderr", b"").decode("utf-8", errors="replace"),
        "stdout_truncated": captured.get("stdout_truncated", False),
        "stderr_truncated": captured.get("stderr_truncated", False),
        "shell_used": False,
        "stdin_inherited": False,
        "secret_environment_variables_forwarded": False,
        "output_capture_memory_limit_per_stream": output_limit_bytes,
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
