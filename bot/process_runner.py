"""Bounded subprocess execution with explicit executable approval and no shell."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Mapping, Sequence

from .tool_registry import TOOL_SPECS
from .system_profile import recommended_child_memory_limit_mb

MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_BYTES = 64_000
SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE_KEY")

# Remove variables that can inject code or native libraries before a tool even
# processes its fixed arguments (for example NODE_OPTIONS=--require ...).
# Compare names case-insensitively because Windows environment keys are too.
CODE_LOADING_ENV_VARS = frozenset({
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT",
    "NODE_OPTIONS", "NODE_PATH",
    "RUBYOPT", "RUBYLIB",
    "PERL5OPT", "PERL5LIB", "PERLLIB",
    "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS",
    "DOTNET_STARTUP_HOOKS",
    "CORECLR_ENABLE_PROFILING", "CORECLR_PROFILER", "CORECLR_PROFILER_PATH",
    "CORECLR_PROFILER_PATH_32", "CORECLR_PROFILER_PATH_64",
    "COR_ENABLE_PROFILING", "COR_PROFILER", "COR_PROFILER_PATH",
    "COR_PROFILER_PATH_32", "COR_PROFILER_PATH_64",
    "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
})


class ProcessRunnerError(ValueError):
    """Raised when a process request violates execution policy."""


def _path_is_same_or_within(path: Path, root: Path) -> bool:
    """Compare normalized path components without confusing sibling prefixes."""
    normalized_path = os.path.normcase(os.path.normpath(str(path)))
    normalized_root = os.path.normcase(os.path.normpath(str(root)))
    if normalized_path == normalized_root:
        return True
    root_prefix = normalized_root.rstrip(os.sep) + os.sep
    return normalized_path.startswith(root_prefix)


def _has_working_tree_ancestor(path: Path, root: Path) -> bool:
    """Detect lexical descendants even when Windows short names differ from resolved paths."""
    current = path
    while True:
        try:
            ancestor_resolved = current.resolve(strict=True)
            if _path_is_same_or_within(ancestor_resolved, root):
                return True
        except (OSError, RuntimeError, ValueError):
            pass
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _safe_search_path(path_value: str | None, *, cwd: str | Path) -> str:
    """Keep absolute existing PATH directories outside cwd and its descendants."""
    safe: list[str] = []
    seen: set[str] = set()
    try:
        current = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return ""
    if not isinstance(path_value, str) or not path_value:
        return ""
    for raw_entry in path_value.split(os.pathsep):
        if not raw_entry or not raw_entry.strip():
            continue
        candidate = Path(raw_entry.strip()).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            # Reject lexical paths under cwd even if a repository-local junction/symlink
            # resolves elsewhere. Also reject external aliases which resolve back into cwd.
            lexical = Path(os.path.normpath(str(candidate)))
            if (
                _path_is_same_or_within(lexical, current)
                or _has_working_tree_ancestor(lexical, current)
            ):
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_dir() or _path_is_same_or_within(resolved, current):
                continue
            resolved_text = str(resolved)
            key = os.path.normcase(os.path.normpath(resolved_text))
            if key in seen:
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        seen.add(key)
        safe.append(resolved_text)
    return os.pathsep.join(safe)


def _find_executable_in_path(executable: str, *, path_value: str | None, cwd: str | Path) -> str | None:
    """Resolve a registered tool using explicit sanitized PATH entries only."""
    if not isinstance(executable, str) or not executable or chr(0) in executable:
        raise ProcessRunnerError("Registered executable name must be a non-empty NUL-free string")
    name = Path(executable)
    if name.is_absolute() or name.parent != Path("."):
        raise ProcessRunnerError("Registered tool names must be basenames, not paths")
    safe_path = _safe_search_path(path_value, cwd=cwd)
    if not safe_path:
        return None
    if os.name == "nt":
        suffixes = [name.suffix] if name.suffix else [".COM", ".EXE", ".BAT", ".CMD"]
    else:
        suffixes = [""]
    for directory in safe_path.split(os.pathsep):
        for suffix in suffixes:
            candidate = Path(directory) / (executable if name.suffix else executable + suffix)
            try:
                if not candidate.is_file():
                    continue
                if os.name != "nt" and not os.access(candidate, os.X_OK):
                    continue
                return str(candidate.resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                continue
    return None


def _safe_environment(
    extra: Mapping[str, str] | None = None,
    *,
    cwd: str | Path | None = None,
) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_MARKERS)
        and key.upper() not in CODE_LOADING_ENV_VARS
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProcessRunnerError("Environment overrides must be string pairs")
            if not key or "=" in key or chr(0) in key:
                raise ProcessRunnerError("Invalid environment variable name")
            if any(marker in key.upper() for marker in SECRET_MARKERS):
                raise ProcessRunnerError("Secret-like environment overrides are forbidden")
            if key.upper() in CODE_LOADING_ENV_VARS:
                raise ProcessRunnerError(f"Code-loading environment override is forbidden: {key}")
            if chr(0) in value:
                raise ProcessRunnerError("Environment values cannot contain NUL")
            env[key] = value
    search_root = Path.cwd() if cwd is None else cwd
    for path_key in tuple(env):
        if path_key.upper() == "PATH":
            env[path_key] = _safe_search_path(env[path_key], cwd=search_root)
    if os.name == "nt":
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return env


def _resolve_approved_executable(executable: str, approved_executables: Sequence[str]) -> str:
    if not isinstance(executable, str) or not executable or chr(0) in executable:
        raise ProcessRunnerError("Executable must be a non-empty string")
    requested = Path(executable).expanduser()
    if not requested.is_absolute():
        raise ProcessRunnerError("Executable must be an absolute path; execution never resolves names through PATH")
    try:
        resolved_path = str(requested.resolve(strict=True))
        if not Path(resolved_path).is_file():
            raise ProcessRunnerError("Executable path does not identify a regular file")
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProcessRunnerError("Executable path could not be resolved to an existing file") from exc
    approved = set()
    for candidate in approved_executables:
        if not isinstance(candidate, str) or not candidate or chr(0) in candidate:
            raise ProcessRunnerError("Allowlisted executable paths must be non-empty NUL-free strings")
        approved_path = Path(candidate).expanduser()
        if not approved_path.is_absolute():
            raise ProcessRunnerError("Every allowlisted executable must be an absolute path")
        try:
            resolved_candidate = str(approved_path.resolve(strict=True))
            if Path(resolved_candidate).is_file():
                approved.add(os.path.normcase(resolved_candidate))
        except (OSError, RuntimeError, ValueError):
            continue
    if os.path.normcase(resolved_path) not in approved:
        raise ProcessRunnerError("Executable is not in the caller's explicit absolute-path allowlist")
    return resolved_path


def _bounded_reader(stream, limit: int, storage: dict, key: str) -> None:
    tail = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > limit:
                del tail[:len(tail) - limit]
                truncated = True
    finally:
        storage[key] = bytes(tail)
        storage[key + "_truncated"] = truncated
        stream.close()


def _terminate_process_tree(process, memory_job) -> bool:
    """Terminate this execution's process group/job, not only its leader.

    Returns True when the Windows Job Object handle was closed and must not
    be closed a second time by the caller.
    """
    if os.name == "nt":
        if memory_job is not None:
            handle, kernel32 = memory_job
            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE stops the leader and every
            # descendant assigned to this job. Close it before joining pipe
            # readers, since a descendant may otherwise keep those pipes open.
            kernel32.CloseHandle(handle)
            return True
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return False

    # Popen uses start_new_session=True on POSIX, making the leader PID the
    # process-group ID. Children inheriting stdout/stderr are killed with it.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    return False


def _attach_windows_memory_job(process, memory_limit_mb: int):
    """Apply a Windows Job Object memory cap and kill descendants when it closes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ProcessRunnerError(f"Could not create Windows memory job (winerror {ctypes.get_last_error()})")
    limit = memory_limit_mb * 1024 * 1024
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.ProcessMemoryLimit = limit
    info.JobMemoryLimit = limit
    if not kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ProcessRunnerError(f"Could not set Windows memory limit (winerror {error})")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ProcessRunnerError(f"Could not assign process to memory job (winerror {error})")
    return job, kernel32

def run_process(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    allowed_root: str | Path,
    approved_executables: Sequence[str],
    timeout_seconds: int = 30,
    output_limit_bytes: int = MAX_OUTPUT_BYTES,
    env_overrides: Mapping[str, str] | None = None,
    memory_limit_mb: int | None = None,
) -> dict:
    """Run an approved executable with bounded output and a Windows Job Object memory cap."""
    if isinstance(argv, (str, bytes)) or not argv:
        raise ProcessRunnerError("argv must be a non-empty sequence of separate arguments")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ProcessRunnerError(f"timeout_seconds must be from 1 to {MAX_TIMEOUT_SECONDS}")
    if not isinstance(output_limit_bytes, int) or isinstance(output_limit_bytes, bool) or not 1 <= output_limit_bytes <= MAX_OUTPUT_BYTES:
        raise ProcessRunnerError(f"output_limit_bytes must be from 1 to {MAX_OUTPUT_BYTES}")
    if memory_limit_mb is None:
        memory_limit_mb = recommended_child_memory_limit_mb()
    if not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool) or not 64 <= memory_limit_mb <= 1024:
        raise ProcessRunnerError("memory_limit_mb must be from 64 to 1024")
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
    child_env = _safe_environment(env_overrides, cwd=workdir)
    captured: dict = {}
    process = None
    memory_job = None
    timed_out = False
    try:
        launch_options = {
            "cwd": workdir,
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        if os.name != "nt":
            # Isolate child processes from BOTPYTON so a timeout can kill the
            # entire execution group without signaling unrelated processes.
            launch_options["start_new_session"] = True
        process = subprocess.Popen(normalized_argv, **launch_options)
        memory_job = _attach_windows_memory_job(process, memory_limit_mb)
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
            if _terminate_process_tree(process, memory_job):
                memory_job = None
            process.wait()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise ProcessRunnerError("Output collector did not stop after process termination")
        exit_code = process.returncode
    except (OSError, ProcessRunnerError) as exc:
        if process is not None:
            if _terminate_process_tree(process, memory_job):
                memory_job = None
            if process.poll() is None:
                process.wait()
        raise ProcessRunnerError(f"Process launch failed or limits could not be applied: {type(exc).__name__}: {exc}") from exc
    finally:
        if memory_job is not None:
            handle, kernel32 = memory_job
            kernel32.CloseHandle(handle)

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
        "process_memory_limit_mb": memory_limit_mb,
        "process_memory_limit_enforced": os.name == "nt",
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
    executable_path = _find_executable_in_path(
        spec.executable,
        path_value=os.environ.get("PATH"),
        cwd=Path.cwd(),
    )
    if executable_path is None:
        raise ProcessRunnerError(
            f"Registered tool is not available on a safe absolute PATH: {tool_id}"
        )
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
