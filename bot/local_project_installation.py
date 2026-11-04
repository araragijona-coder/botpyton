"""Safe, profile-based local installation and quick diagnostics for Python projects.

This first profile is intentionally narrow: it installs the private PDF-to-Markdown
project from a pinned GitHub history, uses Git Credential Manager for authentication,
creates/reuses only a verified Python 3.11 virtual environment, and runs quick only.
"""
from __future__ import annotations

import argparse
import csv
import json
import io
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .process_runner import (
    ProcessRunnerError,
    _attach_windows_memory_job,
    _find_executable_in_path,
    _resolve_approved_executable,
    _safe_environment,
    _terminate_process_tree,
    run_process,
)
from .system_profile import get_system_profile, recommended_child_memory_limit_mb

PROFILE_ID = "pdf-to-markdown-quick"
REPOSITORY = "jonhararagi/botimagen-referencias-privadas"
REPOSITORY_URL = "https://github.com/jonhararagi/botimagen-referencias-privadas.git"
BRANCH = "main"
EXPECTED_COMMIT = "15e8ab50f93a27445f9fb718a55131ad2fd9af65"
PROJECT_SUBDIRECTORY = Path("tools") / "pdf-to-markdown"
VENV_NAME = ".venv"
DIAGNOSTIC_MODULE = "pdf_to_markdown.diagnostics"
DIAGNOSTIC_ARGS = ("--profile-set", "quick")
RESULTS_SUBDIRECTORY = Path("benchmark") / "results" / "performance"
MIN_INSTALL_MEMORY_MB = 768
MIN_INSTALL_DISK_MB = 512
MIN_DIAGNOSTIC_MEMORY_MB = 768
MIN_DIAGNOSTIC_DISK_MB = 256
COMMAND_TIMEOUT_SECONDS = 300
MAX_REPORT_BYTES = 5 * 1024 * 1024
LOG_ROOT_NAME = ".botpyton-install-logs"
PYTHON_RUNTIME_DEPENDENCIES = {
    "pymupdf==2.0.0",
    "psutil>=6.0,<8",
}
BUILD_DEPENDENCIES = {"setuptools>=75"}
VENV_ALLOWED_BASE_PACKAGES = {"pip", "setuptools", "wheel", "pymupdf", "psutil", "pdf-to-markdown-local"}


class LocalProjectInstallationError(ValueError):
    """Raised when the local installation profile cannot proceed safely."""


class _StopInstallation(Exception):
    def __init__(self, phase: str, code: str, message: str):
        self.phase = phase
        self.code = code
        self.message = message
        super().__init__(message)


def _stop(phase: str, code: str, message: str) -> None:
    raise _StopInstallation(phase, code, message)


def _json_output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LocalProjectInstallationError(
            f"Could not safely inspect path component: {type(exc).__name__}"
        ) from exc
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _reject_reparse_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    components = [*reversed(absolute.parents), absolute]
    for component in components:
        if _is_reparse_point(component):
            raise LocalProjectInstallationError(
                "Installation paths cannot contain symlinks, junctions, or reparse points"
            )


def _expand_destination(destination: str | os.PathLike[str]) -> Path:
    if not isinstance(destination, (str, os.PathLike)):
        raise LocalProjectInstallationError("Destination must be a filesystem path")
    raw = os.path.expandvars(os.path.expanduser(os.fspath(destination))).strip()
    if not raw or re.search(r"%[^%]+%", raw):
        raise LocalProjectInstallationError(
            "Destination contains an unresolved environment variable"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise LocalProjectInstallationError("Destination must be an absolute path")
    _reject_reparse_components(path)
    try:
        home = Path.home().resolve(strict=True)
        resolved = path.resolve(strict=False)
        resolved.relative_to(home)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalProjectInstallationError(
            "Destination must remain inside the current user's profile directory"
        ) from exc
    if resolved == home:
        raise LocalProjectInstallationError(
            "Destination cannot be the user profile root itself"
        )
    if path.exists() and not path.is_dir():
        raise LocalProjectInstallationError(
            "Destination already exists and is not a directory; nothing was overwritten"
        )
    return resolved


def _existing_anchor(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise LocalProjectInstallationError("No existing parent directory for destination")
        current = parent
    if not current.is_dir():
        raise LocalProjectInstallationError("Nearest existing destination parent is not a directory")
    _reject_reparse_components(current)
    return current


def _write_new_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_process_logs(log_dir: Path, name: str, result: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for stream_name in ("stdout", "stderr"):
        value = result.get(stream_name)
        if not isinstance(value, str) or not value:
            continue
        path = log_dir / f"{name}.{stream_name}.txt"
        _write_new_text(path, value)
        written.append(str(path))
    return written


def _capture(
    executable: str,
    args: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    allowed_root: Path,
    timeout_seconds: int = 30,
    memory_limit_mb: int | None = None,
    log_dir: Path | None = None,
    log_name: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        result = run_process(
            [executable, *args],
            cwd=cwd,
            allowed_root=allowed_root,
            approved_executables=[executable],
            timeout_seconds=timeout_seconds,
            output_limit_bytes=64_000,
            memory_limit_mb=memory_limit_mb,
            env_overrides=env_overrides,
        )
    except (OSError, ProcessRunnerError) as exc:
        raise _StopInstallation(
            "process_preflight",
            "process_launch_failed",
            f"Could not safely run {Path(executable).name}: {type(exc).__name__}: {exc}",
        ) from exc
    if log_dir is not None and log_name is not None:
        _write_process_logs(log_dir, log_name, result)
    return result


def _capture_text(
    executable: str,
    args: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    allowed_root: Path,
    phase: str,
    code: str,
    timeout_seconds: int = 30,
    memory_limit_mb: int | None = None,
    env_overrides: dict[str, str] | None = None,
) -> str:
    result = _capture(
        executable,
        args,
        cwd=cwd,
        allowed_root=allowed_root,
        timeout_seconds=timeout_seconds,
        memory_limit_mb=memory_limit_mb,
        env_overrides=env_overrides,
    )
    if result["timed_out"]:
        _stop(phase, code, f"Command timed out after {timeout_seconds} seconds")
    if result["exit_code"] != 0:
        _stop(phase, code, f"Command failed with exit code {result['exit_code']}")
    return str(result.get("stdout", "")).strip()


def _git_security_args(hooks_path: Path) -> list[str]:
    """Command-line Git controls take precedence over repository-local config."""
    hooks = hooks_path.resolve(strict=True).as_posix().replace('"', '\\"')
    return [
        "-c", f"core.hooksPath={hooks}",
        "-c", "core.fsmonitor=false",
        "-c", "core.pager=cat",
        "-c", "http.sslVerify=true",
        "-c", "http.https://github.com/.sslVerify=true",
    ]


def _git_environment(config_file: Path) -> dict[str, str]:
    """Use a per-run, minimal Git config and disable system/user config sources."""
    return {
        "GIT_CONFIG_GLOBAL": str(config_file.resolve(strict=True)),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "auto",
    }


def _create_git_security_context(log_dir: Path, helper: str) -> tuple[Path, Path]:
    if helper not in {"manager", "manager-core"}:
        raise LocalProjectInstallationError("Only the verified Git Credential Manager helper is permitted")
    hooks_path = log_dir / "empty-git-hooks"
    hooks_path.mkdir(exist_ok=False)
    config_file = log_dir / "git-security.config"
    hooks_value = hooks_path.resolve(strict=True).as_posix().replace("\\", "/").replace('"', '\\"')
    config_text = (
        "[credential]\n"
        f"    helper = {helper}\n"
        "[core]\n"
        f'    hooksPath = "{hooks_value}"\n'
        "    fsmonitor = false\n"
        "    pager = cat\n"
        "[http]\n"
        "    sslVerify = true\n"
        '[http "https://github.com/"]\n'
        "    sslVerify = true\n"
    )
    with config_file.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(config_text)
    return hooks_path, config_file


def _run_git_interactive(
    git_executable: str,
    args: list[str],
    *,
    cwd: Path,
    allowed_root: Path,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
    memory_limit_mb: int = 512,
    hooks_path: Path,
    config_file: Path,
) -> dict[str, Any]:
    """Run only clone/fetch/pull with inherited console I/O for Git Credential Manager.

    Standard streams are deliberately not captured. GCM may open a browser or
    operating-system sign-in UI, and BotPyton never reads or stores credentials.
    Git's own username/password terminal prompt is disabled.
    """
    if not args or args[0] not in {"clone", "fetch", "pull"}:
        raise LocalProjectInstallationError(
            "Interactive process mode is restricted to Git clone, fetch, and pull"
        )
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 300:
        raise LocalProjectInstallationError("Interactive Git timeout must be from 1 to 300 seconds")
    if not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool) or not 64 <= memory_limit_mb <= 1024:
        raise LocalProjectInstallationError("Interactive Git memory limit must be from 64 to 1024 MB")
    root = allowed_root.resolve(strict=True)
    workdir = cwd.resolve(strict=True)
    try:
        workdir.relative_to(root)
    except ValueError as exc:
        raise LocalProjectInstallationError("Git working directory must be inside the approved root") from exc
    executable = _resolve_approved_executable(git_executable, [git_executable])
    env = _safe_environment(_git_environment(config_file), cwd=workdir)
    git_args = [executable, *_git_security_args(hooks_path), *args]
    options: dict[str, Any] = {
        "cwd": workdir,
        "env": env,
        "stdin": None,
        "stdout": None,
        "stderr": None,
        "shell": False,
        "close_fds": True,
    }
    if os.name != "nt":
        options["start_new_session"] = True

    process: subprocess.Popen[Any] | None = None
    memory_job = None
    timed_out = False
    try:
        process = subprocess.Popen(git_args, **options)
        memory_job = _attach_windows_memory_job(process, memory_limit_mb)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if _terminate_process_tree(process, memory_job):
                memory_job = None
            process.wait()
        return {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
            "process_memory_limit_mb": memory_limit_mb,
            "process_memory_limit_enforced": os.name == "nt",
            "shell_used": False,
            "stdio_inherited": True,
            "output_captured": False,
            "credentials_captured": False,
            "git_hooks_disabled": True,
            "git_system_config_disabled": True,
            "git_user_config_isolated": True,
            "http_tls_verification_enforced": True,
            "network_isolation_enforced": False,
            "child_may_access_network": True,
        }
    except KeyboardInterrupt:
        if process is not None:
            if _terminate_process_tree(process, memory_job):
                memory_job = None
            if process.poll() is None:
                process.wait()
        raise
    except (OSError, ProcessRunnerError) as exc:
        if process is not None:
            if _terminate_process_tree(process, memory_job):
                memory_job = None
            if process.poll() is None:
                process.wait()
        raise LocalProjectInstallationError(
            f"Interactive Git process could not start safely: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if memory_job is not None:
            handle, kernel32 = memory_job
            kernel32.CloseHandle(handle)


def _tool_search_cwd() -> Path:
    """Use a neutral existing directory so PATH sanitization does not hide user-installed tools."""
    if os.name == "nt":
        windows_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
        for candidate in (windows_root / "Fonts", windows_root / "Temp"):
            try:
                resolved = candidate.resolve(strict=True)
                if resolved.is_dir():
                    return resolved
            except (OSError, RuntimeError, ValueError):
                continue
        raise LocalProjectInstallationError(
            "Could not resolve a neutral Windows working directory for safe tool discovery"
        )
    return Path(__file__).resolve().parents[2]


def _check_host(destination: Path, report: dict[str, Any], *, log_dir: Path | None = None) -> tuple[str, str, Path]:
    if os.name != "nt" or platform.system() != "Windows":
        _stop("host_preflight", "windows_required", "This installation profile is supported only on Windows")
    anchor = _existing_anchor(destination.parent)
    system = get_system_profile(anchor)
    available_ram = system.get("available_memory_mb")
    free_disk = system.get("disk_free_mb")
    report["host"] = {
        "os": system.get("os"),
        "system": system.get("system"),
        "machine": system.get("machine"),
        "processor": platform.processor() or system.get("machine"),
        "logical_cpu_count": system.get("cpu_count"),
        "available_memory_mb": available_ram,
        "disk_free_mb": free_disk,
        "botpyton_python_version": system.get("python_version"),
        "required_free_memory_mb": MIN_INSTALL_MEMORY_MB,
        "required_free_disk_mb": MIN_INSTALL_DISK_MB,
    }
    if not isinstance(available_ram, int) or available_ram < MIN_INSTALL_MEMORY_MB:
        _stop(
            "host_preflight",
            "insufficient_memory",
            f"Available RAM is below the {MIN_INSTALL_MEMORY_MB} MB installation safety threshold",
        )
    if not isinstance(free_disk, int) or free_disk < MIN_INSTALL_DISK_MB:
        _stop(
            "host_preflight",
            "insufficient_disk",
            f"Free disk space is below the {MIN_INSTALL_DISK_MB} MB installation threshold",
        )
    try:
        tool_cwd = _tool_search_cwd()
    except LocalProjectInstallationError as exc:
        _stop("host_preflight", "neutral_working_directory_missing", str(exc))
    git = _find_executable_in_path("git", path_value=os.environ.get("PATH"), cwd=tool_cwd)
    launcher = _find_executable_in_path("py", path_value=os.environ.get("PATH"), cwd=tool_cwd)
    if not git:
        _stop("host_preflight", "git_missing", "Git was not found on PATH; no installer was run")
    if not launcher:
        _stop("host_preflight", "python_launcher_missing", "The Windows py launcher was not found on PATH")
    git = str(Path(git).resolve(strict=True))
    launcher = str(Path(launcher).resolve(strict=True))
    if not Path(git).is_file() or not Path(launcher).is_file():
        _stop("host_preflight", "tool_path_invalid", "Git or the Python launcher did not resolve to a regular file")
    memory_limit = recommended_child_memory_limit_mb(
        int(available_ram) * 1024 * 1024
    )
    memory_limit = max(64, min(512, memory_limit))
    git_version = _capture(
        git, ["--version"], cwd=tool_cwd, allowed_root=tool_cwd,
        memory_limit_mb=memory_limit, log_dir=log_dir, log_name="git-version",
    )
    if git_version["timed_out"] or git_version["exit_code"] != 0:
        _stop("host_preflight", "git_version_failed", "Git version check failed")
    python_version = _capture(
        launcher, ["-3.11", "--version"], cwd=tool_cwd, allowed_root=tool_cwd,
        memory_limit_mb=memory_limit, log_dir=log_dir, log_name="python-311-version",
    )
    version_text = (str(python_version.get("stdout", "")) + str(python_version.get("stderr", ""))).strip()
    if python_version["timed_out"] or python_version["exit_code"] != 0 or not re.search(r"\bPython\s+3\.11(?:\.\d+)?\b", version_text):
        _stop(
            "host_preflight",
            "python_311_unavailable",
            "py -3.11 --version did not confirm a Python 3.11 interpreter",
        )
    report["host"]["git_version"] = str(git_version.get("stdout", "")).strip()
    report["host"]["python_311_version"] = version_text
    return git, launcher, anchor


def _validate_repository_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
        repository_path = parsed.path.strip("/")
        if repository_path.endswith(".git"):
            repository_path = repository_path[:-4]
        return repository_path.casefold() == REPOSITORY.casefold()
    except ValueError:
        return False


def _git_capture(
    git: str,
    args: list[str],
    *,
    cwd: Path,
    home: Path,
    phase: str = "git_inspection",
    code: str = "git_command_failed",
    hooks_path: Path,
    env_overrides: dict[str, str],
) -> str:
    safe_args = [*_git_security_args(hooks_path), *args]
    return _capture_text(
        git, safe_args, cwd=cwd, allowed_root=home,
        phase=phase, code=code, timeout_seconds=45, memory_limit_mb=256,
        env_overrides=env_overrides,
    )


def _require_gcm(
    git: str,
    home: Path,
    *,
    cwd: Path | None = None,
    allowed_root: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> str:
    command_cwd = home if cwd is None else cwd
    process_root = home if allowed_root is None else allowed_root
    # Inspect every configured helper. A second helper could receive credentials
    # before/after GCM, so never accept a mixed helper chain.
    config = _capture(
        git, ["config", "--get-all", "credential.helper"],
        cwd=command_cwd, allowed_root=process_root, timeout_seconds=20, memory_limit_mb=256,
        env_overrides=env_overrides,
    )
    if config["timed_out"] or config["exit_code"] != 0:
        _stop(
            "authentication_preflight",
            "git_credential_manager_not_configured",
            "Git Credential Manager is not configured. Configure the official GCM integration manually, complete any sign-in yourself, then retry; BotPyton will not request or store a token.",
        )
    helpers = [line.strip().casefold() for line in str(config.get("stdout", "")).splitlines() if line.strip()]
    if len(helpers) != 1 or helpers[0] not in {"manager", "manager-core"}:
        _stop(
            "authentication_preflight",
            "git_credential_manager_not_exclusive",
            "Git must have exactly one credential.helper entry and it must be Git Credential Manager. Other or multiple helpers are refused.",
        )
    helper = helpers[0]
    url_helpers = _capture(
        git, ["config", "--get-regexp", r"^credential\..+\.helper$"],
        cwd=command_cwd, allowed_root=process_root, timeout_seconds=20,
        memory_limit_mb=256, env_overrides=env_overrides,
    )
    if url_helpers["timed_out"] or url_helpers["exit_code"] not in (0, 1):
        _stop(
            "authentication_preflight",
            "git_url_credential_helpers_unreadable",
            "URL-specific Git credential helpers could not be safely inspected",
        )
    if url_helpers["exit_code"] == 0 and str(url_helpers.get("stdout", "")).strip():
        _stop(
            "authentication_preflight",
            "git_url_credential_helper_forbidden",
            "URL-specific credential helpers are forbidden; only the single verified global Git Credential Manager helper may receive credentials",
        )
    command = "credential-manager" if helper == "manager" else "credential-manager-core"
    result = _capture(
        git, [command, "--version"], cwd=command_cwd, allowed_root=process_root,
        timeout_seconds=20, memory_limit_mb=256, env_overrides=env_overrides,
    )
    if result["timed_out"] or result["exit_code"] != 0:
        _stop(
            "authentication_preflight",
            "git_credential_manager_unavailable",
            "Git is configured for Git Credential Manager, but its executable could not be verified",
        )
    return helper


def _check_git_transport_config(
    git: str,
    cwd: Path,
    home: Path,
    *,
    allowed_root: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    hooks_path: Path | None = None,
    local_only: bool = False,
) -> None:
    """Reject Git settings that can redirect auth or execute external programs."""
    process_root = home if allowed_root is None else allowed_root
    blocked_patterns = (
        r"^url\..*\.insteadof$",
        r"^(http\.extraheader|http\..*\.extraheader|core\.sshcommand)$",
        r"^credential\..+\.helper$",
        r"^filter\..*\.(clean|smudge|process)$",
        r"^(core\.(hooksPath|fsmonitor)|include\..*\.path|includeif\..*\.path)$",
        r"^http\..*\.sslverify$",
    )
    for pattern in blocked_patterns:
        args = ["config"]
        if local_only:
            args.append("--local")
        args.extend(["--get-regexp", pattern])
        if hooks_path is not None:
            args = [*_git_security_args(hooks_path), *args]
        result = _capture(
            git, args,
            cwd=cwd, allowed_root=process_root, timeout_seconds=20, memory_limit_mb=256,
            env_overrides=env_overrides,
        )
        if result["timed_out"] or result["exit_code"] not in (0, 1):
            _stop(
                "authentication_preflight",
                "git_transport_config_unreadable",
                "Git transport, hook, filter, include, or credential configuration could not be safely inspected",
            )
        if result["exit_code"] == 0 and str(result.get("stdout", "")).strip():
            _stop(
                "authentication_preflight",
                "git_custom_transport_config",
                "A Git URL rewrite, custom HTTP/SSH setting, URL-specific credential helper, external filter, hook override, fsmonitor, or config include is configured. Remove it before installation.",
            )


def _assert_checkout_state(
    git: str,
    destination: Path,
    home: Path,
    *,
    hooks_path: Path,
    config_file: Path,
) -> None:
    if not destination.exists() or not destination.is_dir():
        _stop("git_checkout", "checkout_missing", "Repository checkout directory is missing")
    _reject_reparse_components(destination)
    git_metadata = destination / ".git"
    if not git_metadata.is_dir() or _is_reparse_point(git_metadata):
        _stop("git_checkout", "git_metadata_invalid", "Destination does not contain a normal, non-linked Git metadata directory")
    git_env = _git_environment(config_file)
    top = _git_capture(
        git, ["rev-parse", "--show-toplevel"], cwd=destination, home=home,
        phase="git_checkout", code="not_a_git_repository",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    try:
        if Path(top).resolve(strict=True) != destination.resolve(strict=True):
            _stop("git_checkout", "wrong_git_root", "Destination is not the root of the expected Git working tree")
    except (OSError, RuntimeError, ValueError):
        _stop("git_checkout", "wrong_git_root", "Git reported a checkout root that could not be verified")
    remote = _git_capture(
        git, ["remote", "get-url", "origin"], cwd=destination, home=home,
        phase="git_checkout", code="origin_missing",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    if not _validate_repository_url(remote):
        _stop(
            "git_checkout",
            "origin_mismatch",
            "The existing origin does not match the expected HTTPS GitHub repository; nothing was changed",
        )
    branch = _git_capture(
        git, ["branch", "--show-current"], cwd=destination, home=home,
        phase="git_checkout", code="branch_unavailable",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    if branch != BRANCH:
        _stop(
            "git_checkout",
            "wrong_current_branch",
            f"Current branch is {branch!r}, not main; BotPyton will not switch branches",
        )
    status = _git_capture(
        git, ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=destination, home=home, phase="git_checkout",
        code="git_status_failed", hooks_path=hooks_path, env_overrides=git_env,
    )
    if status:
        _stop(
            "git_checkout",
            "dirty_working_tree",
            "The existing checkout has modified or untracked files; BotPyton will not overwrite or clean them",
        )


def _verify_expected_commit(
    git: str,
    destination: Path,
    home: Path,
    ref: str,
    *,
    hooks_path: Path,
    env_overrides: dict[str, str],
) -> None:
    actual = _git_capture(
        git, ["rev-parse", ref], cwd=destination, home=home,
        phase="git_history_validation", code="expected_commit_unavailable",
        hooks_path=hooks_path, env_overrides=env_overrides,
    )
    if actual != EXPECTED_COMMIT:
        _stop(
            "git_history_validation",
            "unapproved_commit_refused",
            f"{ref} resolves to {actual}, not the exact reviewed commit {EXPECTED_COMMIT}; no pull or installation was attempted",
        )


def _prepare_checkout(
    git: str,
    destination: Path,
    home: Path,
    report: dict[str, Any],
    *,
    hooks_path: Path,
    config_file: Path,
) -> str:
    created = False
    git_env = _git_environment(config_file)
    if destination.exists():
        if not destination.is_dir():
            _stop("git_checkout", "checkout_not_directory", "The destination exists but is not a directory")
        _reject_reparse_components(destination)
        git_metadata = destination / ".git"
        if not git_metadata.is_dir() or _is_reparse_point(git_metadata):
            _stop("git_checkout", "git_metadata_invalid", "Destination does not contain a normal, non-linked Git metadata directory")
        _require_gcm(git, home, cwd=destination, allowed_root=home, env_overrides=git_env)
        _check_git_transport_config(
            git, destination, home, env_overrides=git_env,
            hooks_path=hooks_path, local_only=True,
        )
        _assert_checkout_state(
            git, destination, home, hooks_path=hooks_path, config_file=config_file,
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse_components(destination.parent)
        result = _run_git_interactive(
            git,
            ["clone", "--branch", BRANCH, "--single-branch", "--", REPOSITORY_URL, str(destination)],
            cwd=destination.parent,
            allowed_root=home,
            memory_limit_mb=512,
            hooks_path=hooks_path,
            config_file=config_file,
        )
        if result["timed_out"] or result["exit_code"] != 0:
            _stop(
                "git_clone",
                "git_clone_failed",
                "Git clone did not complete. Any partial destination was preserved for inspection; nothing was deleted",
            )
        created = True
        _require_gcm(git, home, cwd=destination, allowed_root=home, env_overrides=git_env)
        _check_git_transport_config(
            git, destination, home, env_overrides=git_env,
            hooks_path=hooks_path, local_only=True,
        )
        _assert_checkout_state(
            git, destination, home, hooks_path=hooks_path, config_file=config_file,
        )

    report["repository"]["checkout_created"] = created
    fetch = _run_git_interactive(
        git, ["fetch", "origin", BRANCH],
        cwd=destination, allowed_root=home, memory_limit_mb=512,
        hooks_path=hooks_path, config_file=config_file,
    )
    if fetch["timed_out"] or fetch["exit_code"] != 0:
        _stop("git_fetch", "git_fetch_failed", "Git fetch failed; local files were not cleaned or reset")
    _verify_expected_commit(
        git, destination, home, "FETCH_HEAD",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    local_is_ancestor = _capture(
        git, [*_git_security_args(hooks_path), "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"],
        cwd=destination, allowed_root=home, timeout_seconds=30, memory_limit_mb=256,
        env_overrides=git_env,
    )
    if local_is_ancestor["timed_out"] or local_is_ancestor["exit_code"] != 0:
        _stop(
            "git_history_validation",
            "local_main_not_fast_forwardable",
            "The local main history cannot be advanced to the exact reviewed commit by fast-forward only; no pull or merge was attempted",
        )

    pull = _run_git_interactive(
        git, ["pull", "--ff-only", "origin", BRANCH],
        cwd=destination, allowed_root=home, memory_limit_mb=512,
        hooks_path=hooks_path, config_file=config_file,
    )
    if pull["timed_out"] or pull["exit_code"] != 0:
        _stop(
            "git_pull",
            "git_fast_forward_failed",
            "git pull --ff-only failed; BotPyton did not merge, reset, or switch branches",
        )
    _assert_checkout_state(
        git, destination, home, hooks_path=hooks_path, config_file=config_file,
    )
    _verify_expected_commit(
        git, destination, home, "HEAD",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    head = _git_capture(
        git, ["rev-parse", "HEAD"], cwd=destination, home=home,
        phase="git_verification", code="head_sha_unavailable",
        hooks_path=hooks_path, env_overrides=git_env,
    )
    report["repository"].update({
        "name": REPOSITORY,
        "branch": BRANCH,
        "expected_commit": EXPECTED_COMMIT,
        "exact_commit_verified": True,
        "local_head_sha": head,
        "origin_verified": True,
        "working_tree_clean_before_install": True,
        "fast_forward_only": True,
        "git_hooks_disabled": True,
        "git_system_config_disabled": True,
        "git_user_config_isolated": True,
        "http_tls_verification_enforced": True,
        "push_performed": False,
        "commit_created": False,
        "pull_request_created": False,
    })
    return head


def _validate_project_files(project_dir: Path) -> dict[str, Any]:
    if (project_dir / "setup.py").exists() or (project_dir / "setup.cfg").exists():
        _stop(
            "project_inspection",
            "unreviewed_setup_hook",
            "setup.py/setup.cfg are not permitted by this fixed installer profile; no project code was executed",
        )
    expected_files = (
        "pyproject.toml",
        "requirements.txt",
        ".gitignore",
        "src/pdf_to_markdown/__init__.py",
        "src/pdf_to_markdown/diagnostics.py",
        "scripts/generate_synthetic_benchmark.py",
    )
    for relative in expected_files:
        path = project_dir / relative
        if not path.is_file() or _is_reparse_point(path):
            _stop(
                "project_inspection",
                "required_project_file_missing",
                f"Required project file is missing or unsafe: {relative}",
            )
    try:
        raw = (project_dir / "pyproject.toml").read_bytes()
        if len(raw) > 200_000:
            raise ValueError("pyproject.toml exceeds the size limit")
        config = tomllib.loads(raw.decode("utf-8"))
        requirements_text = (project_dir / "requirements.txt").read_text(encoding="utf-8")
        ignore_text = (project_dir / ".gitignore").read_text(encoding="utf-8")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        _stop("project_inspection", "project_metadata_invalid", f"Project metadata could not be validated: {type(exc).__name__}")

    build_system = config.get("build-system", {})
    if (
        build_system.get("build-backend") != "setuptools.build_meta"
        or build_system.get("backend-path")
    ):
        _stop(
            "project_inspection",
            "unreviewed_build_backend",
            "Only the reviewed setuptools.build_meta backend without backend-path is permitted",
        )
    setuptools_config = config.get("tool", {}).get("setuptools", {})
    expected_setuptools_config = {
        "package-dir": {"": "src"},
        "packages": {"find": {"where": ["src"]}},
    }
    if setuptools_config != expected_setuptools_config:
        _stop(
            "project_inspection",
            "unreviewed_setuptools_config",
            "Setuptools package configuration differs from the reviewed src-layout profile",
        )
    build_requires = {
        str(item).replace(" ", "").casefold()
        for item in build_system.get("requires", [])
    }
    project = config.get("project", {})
    dependencies = {
        str(item).replace(" ", "").casefold()
        for item in project.get("dependencies", [])
    }
    declared_requirements = {
        line.strip().replace(" ", "").casefold()
        for line in requirements_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if project.get("name") != "pdf-to-markdown-local":
        _stop("project_inspection", "unexpected_project_name", "The project name does not match the PDF-to-Markdown profile")
    if not str(project.get("requires-python", "")).startswith(">=") or "3.11" not in str(project.get("requires-python", "")):
        _stop("project_inspection", "python_requirement_mismatch", "Project metadata does not declare Python 3.11 or newer")
    if build_requires != BUILD_DEPENDENCIES:
        _stop("project_inspection", "unexpected_build_dependencies", "Build dependencies differ from the reviewed setuptools requirement")
    if dependencies != PYTHON_RUNTIME_DEPENDENCIES or declared_requirements != PYTHON_RUNTIME_DEPENDENCIES:
        _stop(
            "project_inspection",
            "unexpected_runtime_dependencies",
            "Runtime dependencies differ from the reviewed PyMuPDF/psutil allowlist; no packages were installed",
        )
    scripts = project.get("scripts", {})
    if scripts.get("pdf-to-markdown-diagnostics") != "pdf_to_markdown.diagnostics:main":
        _stop("project_inspection", "diagnostic_entrypoint_mismatch", "The expected diagnostic entry point is missing")
    ignored = {line.strip().replace("\\", "/") for line in ignore_text.splitlines()}
    if ".venv/" not in ignored or "benchmark/results/" not in ignored:
        _stop(
            "project_inspection",
            "local_artifacts_not_ignored",
            "The project does not ignore its .venv and benchmark/results outputs; installation was stopped to avoid accidental publication",
        )
    return {
        "project_name": project["name"],
        "runtime_dependencies": sorted(dependencies),
        "build_dependencies": sorted(build_requires),
        "optional_dependencies_installed": [],
        "ocr_installed": False,
        "docling_installed": False,
        "tesseract_installed": False,
        "source_files_modified": False,
    }


def _venv_inspection_script() -> str:
    return (
        "import importlib.metadata as m, importlib.util, json; "
        "spec=importlib.util.find_spec('pdf_to_markdown'); "
        "try_dist=None; "
        "exec(\"try:\\n try_dist=m.distribution('pdf-to-markdown-local')\\n"
        "except m.PackageNotFoundError:\\n try_dist=None\"); "
        "print(json.dumps({"
        "'installed': try_dist is not None, "
        "'version': None if try_dist is None else try_dist.version, "
        "'direct_url': None if try_dist is None else try_dist.read_text('direct_url.json'), "
        "'module_file': None if spec is None else spec.origin, "
        "'packages': sorted({d.metadata.get('Name','').casefold() for d in m.distributions() if d.metadata.get('Name')}), "
        "'package_versions': {d.metadata.get('Name','').casefold(): d.version for d in m.distributions() if d.metadata.get('Name')}"
        "}))"
    )


def _project_is_editable_install(info: dict[str, Any], project_dir: Path) -> bool:
    if info.get("installed") is not True or info.get("version") != "0.1.0":
        return False
    module_file = info.get("module_file")
    direct_url_text = info.get("direct_url")
    if not isinstance(module_file, str) or not isinstance(direct_url_text, str):
        return False
    try:
        module_path = Path(module_file).resolve(strict=True)
        module_path.relative_to((project_dir / "src" / "pdf_to_markdown").resolve(strict=True))
        direct_url = json.loads(direct_url_text)
        url = direct_url.get("url", "")
        parsed = urlsplit(url)
        direct_path = parsed.path
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", direct_path):
            direct_path = direct_path[1:]
        source_path = Path(direct_path).resolve(strict=True)
        editable = direct_url.get("dir_info", {}).get("editable") is True
        return editable and source_path == project_dir.resolve(strict=True)
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _verify_installed_environment(
    python: str,
    project_dir: Path,
    log_dir: Path,
    memory_limit_mb: int,
) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m, json, pdf_to_markdown, pymupdf, psutil; "
        "print(json.dumps({"
        "'distribution': m.version('pdf-to-markdown-local'), "
        "'pymupdf': m.version('PyMuPDF'), "
        "'psutil': m.version('psutil'), "
        "'module_file': pdf_to_markdown.__file__"
        "}))"
    )
    result = _capture(
        python, ["-c", code], cwd=project_dir, allowed_root=project_dir,
        timeout_seconds=30, memory_limit_mb=memory_limit_mb,
        log_dir=log_dir, log_name="verify-imports",
    )
    if result["timed_out"] or result["exit_code"] != 0:
        _stop("dependency_verification", "package_import_failed", "The installed package or one of its declared dependencies could not be imported")
    try:
        info = json.loads(result.get("stdout", ""))
    except json.JSONDecodeError:
        _stop("dependency_verification", "package_import_report_invalid", "Package import verification did not return valid JSON")
    if info.get("distribution") != "0.1.0" or info.get("pymupdf") != "2.0.0":
        _stop("dependency_verification", "package_version_mismatch", "Installed distribution or PyMuPDF version differs from the reviewed profile")
    psutil_version = str(info.get("psutil", ""))
    match = re.match(r"^(\d+)\.(\d+)", psutil_version)
    if not match or not 6 <= int(match.group(1)) < 8:
        _stop("dependency_verification", "psutil_version_mismatch", "Installed psutil version is outside the declared 6.x–7.x range")
    try:
        module_path = Path(str(info.get("module_file"))).resolve(strict=True)
        module_path.relative_to((project_dir / "src" / "pdf_to_markdown").resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        _stop("dependency_verification", "package_import_path_mismatch", "The imported module did not resolve to this checkout's src directory")
    pip_check = _capture(
        python, ["-m", "pip", "check"], cwd=project_dir, allowed_root=project_dir,
        timeout_seconds=30, memory_limit_mb=memory_limit_mb,
        log_dir=log_dir, log_name="pip-check",
    )
    if pip_check["timed_out"] or pip_check["exit_code"] != 0:
        _stop("dependency_verification", "pip_check_failed", "pip check reported broken or incompatible dependencies")
    help_result = _capture(
        python, ["-m", DIAGNOSTIC_MODULE, "--help"],
        cwd=project_dir, allowed_root=project_dir,
        timeout_seconds=30, memory_limit_mb=memory_limit_mb,
        log_dir=log_dir, log_name="diagnostic-help",
    )
    if help_result["timed_out"] or help_result["exit_code"] != 0:
        _stop("dependency_verification", "diagnostic_entrypoint_unavailable", "The quick diagnostic module is not available")
    return info


def _prepare_venv(
    launcher: str,
    project_dir: Path,
    log_dir: Path,
    report: dict[str, Any],
    memory_limit_mb: int,
) -> tuple[str, bool]:
    venv_dir = project_dir / VENV_NAME
    _reject_reparse_components(venv_dir)
    python = venv_dir / "Scripts" / "python.exe"
    created = False
    if venv_dir.exists():
        if not venv_dir.is_dir() or not (venv_dir / "pyvenv.cfg").is_file() or not python.is_file():
            _stop(
                "virtual_environment",
                "existing_venv_invalid",
                "An existing .venv is not a valid Windows Python virtual environment; it was preserved",
            )
        version = _capture(
            str(python), ["--version"], cwd=project_dir, allowed_root=project_dir,
            timeout_seconds=20, memory_limit_mb=memory_limit_mb,
            log_dir=log_dir, log_name="existing-venv-version",
        )
        text = (str(version.get("stdout", "")) + str(version.get("stderr", ""))).strip()
        if version["timed_out"] or version["exit_code"] != 0 or not re.search(r"\bPython\s+3\.11(?:\.\d+)?\b", text):
            _stop("virtual_environment", "existing_venv_python_mismatch", "Existing .venv is not running Python 3.11; it was preserved")
    else:
        created_result = _capture(
            launcher, ["-3.11", "-m", "venv", VENV_NAME],
            cwd=project_dir, allowed_root=project_dir,
            timeout_seconds=120, memory_limit_mb=memory_limit_mb,
            log_dir=log_dir, log_name="create-venv",
        )
        if created_result["timed_out"] or created_result["exit_code"] != 0:
            _stop("virtual_environment", "venv_creation_failed", "Python 3.11 could not create .venv; no existing folder was deleted")
        created = True
        if not python.is_file():
            _stop("virtual_environment", "venv_python_missing", "The new virtual environment did not contain Scripts/python.exe")

    pip_version = _capture(
        str(python), ["-m", "pip", "--version"],
        cwd=project_dir, allowed_root=project_dir,
        timeout_seconds=20, memory_limit_mb=memory_limit_mb,
        log_dir=log_dir, log_name="venv-pip-version",
    )
    if pip_version["timed_out"] or pip_version["exit_code"] != 0:
        _stop("virtual_environment", "venv_pip_missing", "The virtual environment has no working pip module")

    info_result = _capture(
        str(python), ["-c", _venv_inspection_script()],
        cwd=project_dir, allowed_root=project_dir,
        timeout_seconds=30, memory_limit_mb=memory_limit_mb,
        log_dir=log_dir, log_name="inspect-existing-install",
    )
    if info_result["timed_out"] or info_result["exit_code"] != 0:
        _stop("virtual_environment", "venv_inspection_failed", "Could not safely inspect packages in the existing virtual environment")
    try:
        info = json.loads(info_result.get("stdout", ""))
    except json.JSONDecodeError:
        _stop("virtual_environment", "venv_inspection_invalid", "Virtual environment inspection returned invalid JSON")

    installed_same_project = _project_is_editable_install(info, project_dir)
    package_versions = info.get("package_versions", {})
    installed_packages = set(info.get("packages", []))
    unexpected_packages = installed_packages - VENV_ALLOWED_BASE_PACKAGES
    if unexpected_packages:
        _stop(
            "virtual_environment",
            "existing_venv_contains_unreviewed_packages",
            "The existing .venv contains packages outside the reviewed runtime/build allowlist; it was preserved",
        )
    existing_pymupdf = package_versions.get("pymupdf")
    if existing_pymupdf is not None and existing_pymupdf != "2.0.0":
        _stop(
            "virtual_environment",
            "existing_pymupdf_version_conflict",
            "The existing .venv contains a different PyMuPDF version; it was preserved rather than overwritten",
        )
    existing_psutil = package_versions.get("psutil")
    if existing_psutil is not None:
        psutil_match = re.match(r"^(\d+)\.(\d+)", str(existing_psutil))
        if not psutil_match or not 6 <= int(psutil_match.group(1)) < 8:
            _stop(
                "virtual_environment",
                "existing_psutil_version_conflict",
                "The existing .venv contains a psutil version outside the project's declared 6.x–7.x range; it was preserved",
            )
    if info.get("installed") and not installed_same_project:
        _stop(
            "virtual_environment",
            "existing_installation_conflict",
            "The existing virtual environment contains a different or non-editable PDF-to-Markdown installation; it was not overwritten",
        )
    report["virtual_environment"] = {
        "path": str(venv_dir),
        "python": str(python),
        "created": created,
        "reused": not created,
        "existing_project_installation_reused": installed_same_project,
        "preexisting_packages": sorted(installed_packages),
        "preexisting_packages_modified": False,
    }
    return str(python), installed_same_project


def _read_bounded_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
        if not raw or len(raw) > MAX_REPORT_BYTES:
            raise ValueError("report is empty or exceeds the size limit")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("report root is not an object")
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _StopInstallation(
            "result_validation",
            "performance_json_invalid",
            f"Performance JSON could not be read safely: {type(exc).__name__}",
        ) from exc


def _collect_quick_results(
    project_dir: Path,
    previous_run_dirs: set[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    results_root = project_dir / RESULTS_SUBDIRECTORY
    if not results_root.is_dir() or _is_reparse_point(results_root):
        _stop("result_validation", "performance_directory_missing", "Quick diagnostic did not create its performance results directory")
    new_runs = sorted(
        child for child in results_root.iterdir()
        if child.is_dir() and child.name not in previous_run_dirs and not _is_reparse_point(child)
    )
    if len(new_runs) != 1:
        _stop(
            "result_validation",
            "new_performance_run_ambiguous",
            "Could not identify exactly one new performance run created by this invocation",
        )
    run_dir = new_runs[0]
    json_path = run_dir / "performance-report.json"
    csv_path = run_dir / "performance-report.csv"
    for path in (json_path, csv_path):
        if not path.is_file() or _is_reparse_point(path) or path.stat().st_size == 0:
            _stop("result_validation", "performance_report_missing", f"Expected non-empty local report is missing: {path.name}")
    summary = _read_bounded_json(json_path)
    try:
        with csv_path.open("rb") as handle:
            csv_bytes = handle.read(MAX_REPORT_BYTES + 1)
        if len(csv_bytes) > MAX_REPORT_BYTES:
            _stop("result_validation", "performance_csv_too_large", "Performance CSV exceeds the report size limit")
        csv_text = csv_bytes.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(csv_text)))
    except (OSError, UnicodeError, csv.Error) as exc:
        _stop("result_validation", "performance_csv_invalid", f"Performance CSV could not be read: {type(exc).__name__}")
    if csv_path.stat().st_size > MAX_REPORT_BYTES:
        _stop("result_validation", "performance_csv_too_large", "Performance CSV exceeds the report size limit")
    if (
        summary.get("tool") != "pdf-to-markdown-local-diagnostics"
        or summary.get("metadata", {}).get("profile_set") != "quick"
        or summary.get("run_id") != run_dir.name
        or summary.get("metadata", {}).get("fixture_name") != "F-12-batch-120-pages.pdf"
        or summary.get("metadata", {}).get("privacy", {}).get("synthetic_fixture_only") is not True
        or summary.get("metadata", {}).get("privacy", {}).get("network_used_by_diagnostic") is not False
        or summary.get("fixture_unchanged") is not True
        or len(summary.get("results", [])) != 1
        or len(rows) != 1
    ):
        _stop("result_validation", "performance_report_mismatch", "JSON/CSV do not describe one validated quick run over the synthetic fixture")
    json_row = summary["results"][0]
    csv_row = rows[0]
    required_fields = ("profile", "page_mode", "status", "execution_ok")
    if any(field not in json_row or field not in csv_row for field in required_fields):
        _stop(
            "result_validation",
            "performance_report_fields_missing",
            "JSON and CSV must both provide profile, page_mode, status, and execution_ok",
        )
    for field, csv_value in csv_row.items():
        json_value = json_row.get(field)
        expected_csv_value = "" if json_value is None else (
            "True" if json_value is True else "False" if json_value is False else str(json_value)
        )
        if csv_value != expected_csv_value:
            _stop(
                "result_validation",
                "performance_reports_disagree",
                f"JSON and CSV disagree on the {field!r} field",
            )
    if summary.get("stopped_by_safety_guard") is True:
        if (
            json_row.get("status") != "skipped_safety_guard"
            or csv_row.get("status") != "skipped_safety_guard"
            or json_row.get("page_mode") != "first20"
            or csv_row.get("page_mode") != "first20"
            or json_row.get("execution_ok") is not False
            or csv_row.get("execution_ok") != "False"
            or summary.get("overall_status") != "partial"
        ):
            _stop(
                "result_validation",
                "safety_guard_report_mismatch",
                "The diagnostic reported a safety stop but the JSON/CSV rows do not match that stop",
            )
    elif (
        json_row.get("profile") != csv_row.get("profile")
        or json_row.get("page_mode") != "first20"
        or csv_row.get("page_mode") != "first20"
        or json_row.get("execution_ok") is not True
        or csv_row.get("execution_ok") != "True"
        or json_row.get("status") != "complete"
        or json_row.get("page_markers_valid") is not True
        or json_row.get("source_hash_unchanged") is not True
    ):
        _stop("result_validation", "quick_profile_incomplete", "The quick profile did not pass its page-marker and source-integrity checks")
    paths = {"json": str(json_path.resolve()), "csv": str(csv_path.resolve())}
    return summary, paths


def _persist_report(log_dir: Path, report: dict[str, Any]) -> str | None:
    try:
        path = log_dir / "installation-report.json"
        _write_new_text(path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return str(path)
    except (OSError, LocalProjectInstallationError):
        return None


def run_local_project_installation(
    profile_id: str = PROFILE_ID,
    destination: str | os.PathLike[str] = r"%USERPROFILE%\source\botimagen-referencias-privadas",
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or execute the fixed PDF-to-Markdown quick installation profile."""
    if profile_id != PROFILE_ID:
        raise LocalProjectInstallationError(f"Unknown local installation profile: {profile_id}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    report: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": PROFILE_ID,
        "run_id": run_id,
        "status": "preview" if not execute else "running",
        "execution_authorized": execute is True,
        "repository": {
            "name": REPOSITORY,
            "url": REPOSITORY_URL,
            "branch": BRANCH,
            "expected_commit": EXPECTED_COMMIT,
        },
        "destination": None,
        "steps": [],
        "network": {
            "github_clone_or_fetch_may_occur": execute is True,
            "pypi_dependency_download_may_occur": execute is True,
            "pdf_conversion_service_used": False,
            "network_isolation_enforced": False,
        },
        "security": {
            "personal_pdfs_used": False,
            "pdf_or_conversion_uploaded": False,
            "github_push_performed": False,
            "github_commit_created": False,
            "github_pull_request_created": False,
            "source_files_modified_by_installer": False,
            "preexisting_files_deleted": False,
            "credentials_captured_or_stored_by_botpyton": False,
            "git_authentication": "Git Credential Manager only; standard streams are not captured",
            "diagnostic_profile": "quick",
            "diagnostic_parallelism": 1,
            "multiprocessing_requested": False,
            "ocr_enabled": False,
            "docling_requested": False,
            "tesseract_requested": False,
            "cuda_requested": False,
            "standard_profile_executed": False,
        },
    }
    try:
        target = _expand_destination(destination)
        report["destination"] = str(target)
        if not execute:
            anchor = _existing_anchor(target.parent)
            system = get_system_profile(anchor)
            report["host"] = {
                "os": system.get("os"),
                "system": system.get("system"),
                "processor": system.get("processor"),
                "logical_cpu_count": system.get("cpu_count"),
                "available_memory_mb": system.get("available_memory_mb"),
                "disk_free_mb": system.get("disk_free_mb"),
                "git_found": _find_executable_in_path("git", path_value=os.environ.get("PATH"), cwd=_tool_search_cwd()) is not None,
                "py_launcher_found": _find_executable_in_path("py", path_value=os.environ.get("PATH"), cwd=Path(__file__).resolve().parents[2]) is not None,
            }
            report["next_command"] = (
                "python -m bot.local_project_installation --profile pdf-to-markdown-quick --execute"
            )
            report["steps"].append({"name": "read_only_preview", "status": "complete"})
            return report

        # Check resources and required executables before creating any directory.
        git, launcher, anchor = _check_host(target, report)
        report["steps"].append({"name": "host_preflight", "status": "complete"})

        home = Path.home().resolve(strict=True)
        tool_cwd = _tool_search_cwd()
        gcm_helper = _require_gcm(git, home, cwd=tool_cwd, allowed_root=tool_cwd)
        _check_git_transport_config(git, tool_cwd, home, allowed_root=tool_cwd)
        report["steps"].append({"name": "git_credential_manager_preflight", "status": "complete"})

        # A unique local log folder preserves non-secret process output and never overwrites a prior run.
        logs_root = target.parent / LOG_ROOT_NAME / PROFILE_ID
        _reject_reparse_components(logs_root)
        logs_root.mkdir(parents=True, exist_ok=True)
        log_dir = logs_root / run_id
        log_dir.mkdir(exist_ok=False)
        report["log_directory"] = str(log_dir)
        hooks_path, git_config_file = _create_git_security_context(log_dir, gcm_helper)
        report["git_security"] = {
            "hooks_path": str(hooks_path),
            "isolated_global_config": str(git_config_file),
            "system_config_disabled": True,
            "user_config_isolated": True,
            "external_hooks_disabled": True,
            "fsmonitor_disabled": True,
            "tls_verification_forced": True,
        }

        _prepare_checkout(
            git, target, home, report,
            hooks_path=hooks_path, config_file=git_config_file,
        )
        report["steps"].append({"name": "git_checkout_fast_forward", "status": "complete"})

        project_dir = target / PROJECT_SUBDIRECTORY
        _reject_reparse_components(project_dir)
        report["project"] = _validate_project_files(project_dir)
        report["steps"].append({"name": "project_metadata_and_allowlist", "status": "complete"})

        available_ram = report["host"]["available_memory_mb"]
        memory_limit = recommended_child_memory_limit_mb(int(available_ram) * 1024 * 1024)
        memory_limit = max(64, min(768, memory_limit))
        python, already_installed = _prepare_venv(
            launcher, project_dir, log_dir, report, memory_limit
        )
        report["steps"].append({"name": "python_311_virtual_environment", "status": "complete"})

        if not already_installed:
            pip = _capture(
                python,
                [
                    "-m", "pip", "--isolated", "install",
                    "--no-cache-dir", "--disable-pip-version-check", "--no-input", "-e", ".",
                ],
                cwd=project_dir, allowed_root=project_dir,
                timeout_seconds=COMMAND_TIMEOUT_SECONDS,
                memory_limit_mb=memory_limit,
                log_dir=log_dir, log_name="pip-editable-install",
            )
            if pip["timed_out"] or pip["exit_code"] != 0:
                _stop(
                    "dependency_installation",
                    "pip_editable_install_failed",
                    "pip install -e . failed. The virtual environment and logs were preserved; no folders were deleted",
                )
            report["dependencies"] = {
                "installation": "editable_install_completed",
                "command": "python -m pip --isolated install --no-cache-dir --disable-pip-version-check --no-input -e .",
                "declared_runtime_dependencies_only": True,
                "build_backend_dependency_allowed": "setuptools>=75",
            }
        else:
            report["dependencies"] = {
                "installation": "existing_editable_installation_reused",
                "declared_runtime_dependencies_only": True,
            }

        installed = _verify_installed_environment(python, project_dir, log_dir, memory_limit)
        report["dependencies"].update({
            "distribution": installed.get("distribution"),
            "pymupdf": installed.get("pymupdf"),
            "psutil": installed.get("psutil"),
            "imports_verified": True,
            "pip_check_passed": True,
            "diagnostic_module_available": True,
        })
        report["steps"].append({"name": "dependency_and_entrypoint_verification", "status": "complete"})

        # Re-check the diagnostic's own safety thresholds immediately before execution.
        live = get_system_profile(project_dir)
        ram_now = live.get("available_memory_mb")
        disk_now = live.get("disk_free_mb")
        report["diagnostic_preflight"] = {
            "available_memory_mb": ram_now,
            "required_memory_mb": MIN_DIAGNOSTIC_MEMORY_MB,
            "disk_free_mb": disk_now,
            "required_disk_mb": MIN_DIAGNOSTIC_DISK_MB,
        }
        if not isinstance(ram_now, int) or ram_now < MIN_DIAGNOSTIC_MEMORY_MB:
            _stop("diagnostic_preflight", "insufficient_memory_for_quick", "Available RAM fell below the quick diagnostic safety threshold")
        if not isinstance(disk_now, int) or disk_now < MIN_DIAGNOSTIC_DISK_MB:
            _stop("diagnostic_preflight", "insufficient_disk_for_quick", "Free disk space fell below the quick diagnostic safety threshold")

        results_root = project_dir / RESULTS_SUBDIRECTORY
        if results_root.exists():
            _reject_reparse_components(results_root)
            previous_runs = {item.name for item in results_root.iterdir() if item.is_dir()}
        else:
            previous_runs = set()
        diagnostic_started = time.monotonic()
        diagnostic = _capture(
            python,
            ["-m", DIAGNOSTIC_MODULE, *DIAGNOSTIC_ARGS],
            cwd=project_dir, allowed_root=project_dir,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            memory_limit_mb=max(64, min(768, recommended_child_memory_limit_mb(int(ram_now) * 1024 * 1024))),
            log_dir=log_dir, log_name="diagnostic-quick",
        )
        diagnostic_elapsed = round(time.monotonic() - diagnostic_started, 3)
        report["diagnostic_process"] = {
            "exit_code": diagnostic["exit_code"],
            "timed_out": diagnostic["timed_out"],
            "elapsed_seconds": diagnostic_elapsed,
            "command": "python -m pdf_to_markdown.diagnostics --profile-set quick",
            "memory_limit_mb": diagnostic.get("process_memory_limit_mb"),
            "process_memory_limit_enforced": diagnostic.get("process_memory_limit_enforced"),
            "stdout_log": next((p for p in (log_dir / "diagnostic-quick.stdout.txt",) if p.is_file()), None),
            "stderr_log": next((p for p in (log_dir / "diagnostic-quick.stderr.txt",) if p.is_file()), None),
        }
        if diagnostic["timed_out"]:
            _stop("diagnostic_quick", "diagnostic_timed_out", "The quick diagnostic exceeded its process time limit")
        if diagnostic["exit_code"] not in (0, 1):
            _stop("diagnostic_quick", "diagnostic_failed", f"The quick diagnostic exited with code {diagnostic['exit_code']}")

        summary, result_paths = _collect_quick_results(project_dir, previous_runs)
        row = summary["results"][0]
        report["diagnostic"] = {
            "overall_status": summary.get("overall_status"),
            "profile_set": summary.get("profile_set"),
            "run_id": summary.get("run_id"),
            "pages_total": row.get("total_pages"),
            "pages_selected": row.get("selected_pages"),
            "pages_failed": row.get("failed_pages"),
            "image_only_pages": row.get("image_only_pages"),
            "wall_seconds": row.get("wall_seconds"),
            "conversion_seconds": row.get("conversion_seconds"),
            "cpu_one_core_percent": row.get("cpu_one_core_percent"),
            "cpu_machine_percent": row.get("cpu_machine_percent"),
            "peak_rss_mb": row.get("peak_rss_mb"),
            "peak_working_set_mb": row.get("peak_working_set_mb"),
            "available_ram_start_mb": row.get("available_ram_start_mb"),
            "available_ram_end_mb": row.get("available_ram_end_mb"),
            "disk_free_start_mb": row.get("disk_free_start_mb"),
            "disk_free_end_mb": row.get("disk_free_end_mb"),
            "page_markers_valid": row.get("page_markers_valid"),
            "source_hash_unchanged": row.get("source_hash_unchanged"),
            "safety_guard_stopped": summary.get("stopped_by_safety_guard"),
        }
        report["performance_reports"] = result_paths
        report["steps"].append({"name": "quick_synthetic_diagnostic", "status": summary.get("overall_status")})

        final_status = "succeeded" if summary.get("overall_status") == "complete" and diagnostic["exit_code"] == 0 else "partial"
        report["status"] = final_status
        if summary.get("stopped_by_safety_guard") is True:
            report["error_code"] = "diagnostic_safety_guard"
            report["error"] = "The quick diagnostic stopped safely because live RAM or disk space fell below its internal threshold. The reports were retained and no further work was started."

        final_git_status = _git_capture(
            git, ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=target, home=home,
            phase="final_verification", code="final_git_status_failed",
        )
        report["repository"]["working_tree_clean_after_install"] = not bool(final_git_status)
        report["repository"]["local_uncommitted_paths"] = [
            line[3:] for line in final_git_status.splitlines() if len(line) > 3
        ]
        report["security"]["remote_repository_modified"] = False
        report["security"]["source_files_modified_by_installer"] = bool(final_git_status)
        report["security"]["user_pdf_opened"] = False
        report["security"]["result_files_uploaded"] = False
        report["security"]["network_used_for"] = ["private GitHub checkout/fetch", "PyPI declared dependency installation"]
        report["security"]["network_used_by_pdf_diagnostic"] = summary.get("metadata", {}).get("privacy", {}).get("network_used_by_diagnostic") is True
        report["security"]["no_standard_or_conversion_run"] = True
        if final_git_status:
            report["status"] = "partial"
            report["warnings"] = [
                "The final Git working tree is not clean. No files were deleted or reverted; inspect local_uncommitted_paths."
            ]
        report["report_file"] = _persist_report(log_dir, report)
        return report

    except _StopInstallation as exc:
        report["status"] = "blocked" if exc.code not in {"diagnostic_failed"} else "failed"
        report["failed_phase"] = exc.phase
        report["error_code"] = exc.code
        report["error"] = exc.message
        if report.get("log_directory"):
            report["report_file"] = _persist_report(Path(report["log_directory"]), report)
        return report
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        report["error_code"] = "user_interrupted"
        report["error"] = "Installation was interrupted. No automatic cleanup or deletion was attempted."
        if report.get("log_directory"):
            report["report_file"] = _persist_report(Path(report["log_directory"]), report)
        return report
    except (OSError, RuntimeError, ValueError, ProcessRunnerError) as exc:
        report["status"] = "blocked"
        report["failed_phase"] = "unexpected_safe_stop"
        report["error_code"] = type(exc).__name__
        report["error"] = str(exc)[:1000]
        if report.get("log_directory"):
            report["report_file"] = _persist_report(Path(report["log_directory"]), report)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="botpyton-local-installation",
        description="Install and run one reviewed, bounded local project profile; no shell commands from task text.",
    )
    parser.add_argument("--profile", choices=(PROFILE_ID,), default=PROFILE_ID)
    parser.add_argument(
        "--destination",
        default=r"%USERPROFILE%\source\botimagen-referencias-privadas",
        help="Local checkout destination inside the current user's profile",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Authorize clone/update, Python 3.11 venv preparation, editable dependency install, and quick only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_local_project_installation(args.profile, args.destination, execute=args.execute)
    _json_output(report)
    if report.get("status") in {"preview", "succeeded"}:
        return 0
    if report.get("status") == "partial":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
