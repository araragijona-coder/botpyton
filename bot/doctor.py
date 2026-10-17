"""Read-only preflight that turns common setup problems into concrete next steps."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Callable

from .system_profile import _available_memory_bytes

CORE_FILES = ("bot/main.py", "bot/pc_care.py", "bot/policy.py", "bot/recovery.py")
LOW_MEMORY_WARN_MB = 512
LOW_MEMORY_BLOCK_MB = 256
LOW_DISK_WARN_MB = 1024


def run_preflight(
    project_root: str | Path = ".",
    *,
    python_version: tuple[int, int] | None = None,
    platform_name: str | None = None,
    available_memory_bytes: int | None = None,
    disk_free_bytes: int | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict:
    """Check runtime prerequisites without launching tools, writing files, or using the network."""
    root = Path(project_root).expanduser()
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return {
            "schema_version": 1,
            "status": "blocked",
            "ready": False,
            "read_only": True,
            "network_used": False,
            "checks": [{"id": "project_root", "status": "fail", "message": "The project directory does not exist or cannot be resolved.",
                        "next_action": "Open BOTPYTON.bat from the extracted BOTPYTON folder, or run this command from that folder."}],
            "next_actions": ["Select the correct BOTPYTON project folder and run the doctor again."],
            "operations_launched": 0,
            "files_written": 0,
        }
    version = python_version if python_version is not None else (sys.version_info.major, sys.version_info.minor)
    system = platform_name if platform_name is not None else platform.system()
    memory = _available_memory_bytes() if available_memory_bytes is None else available_memory_bytes
    checker = shutil.which if which is None else which
    checks: list[dict] = []

    def add(check_id: str, status: str, message: str, next_action: str | None = None) -> None:
        item = {"id": check_id, "status": status, "message": message}
        if next_action:
            item["next_action"] = next_action
        checks.append(item)

    if version < (3, 11):
        add("python_version", "fail", f"Python {version[0]}.{version[1]} is too old; Python 3.11 or later is required.",
            "Install Python 3.11+ from python.org, then run with the matching installed Python launcher/version.")
    else:
        add("python_version", "pass", f"Python {version[0]}.{version[1]} meets the minimum version.")

    missing = [relative for relative in CORE_FILES if not (root / relative).is_file()]
    if missing:
        add("project_files", "fail", "Required BOTPYTON files are missing: " + ", ".join(missing[:4]),
            "Do not recreate files by guessing. Re-extract or update the official repository checkout, then rerun --doctor.")
    else:
        add("project_files", "pass", "Required core files are present.")

    if memory is None or memory <= 0:
        add("available_memory", "warn", "Available RAM could not be measured.",
            "Close unnecessary applications and avoid running project builds or multiple checks simultaneously.")
    else:
        memory_mb = memory // (1024 * 1024)
        if memory_mb < LOW_MEMORY_BLOCK_MB:
            add("available_memory", "fail", f"Only {memory_mb} MiB RAM appears available; intensive work should not start.",
                "Close memory-heavy applications, wait for Windows to settle, and rerun --doctor before tests/builds.")
        elif memory_mb < LOW_MEMORY_WARN_MB:
            add("available_memory", "warn", f"Only {memory_mb} MiB RAM appears available; run one bounded operation at a time.",
                "Avoid builds and duplicate scans until more RAM is available; do not launch parallel workers.")
        else:
            add("available_memory", "pass", f"Approximately {memory_mb} MiB RAM is available.")

    if disk_free_bytes is None:
        try:
            disk_free_bytes = int(shutil.disk_usage(root).free)
        except OSError:
            disk_free_bytes = None
    if disk_free_bytes is None:
        add("disk_space", "warn", "Free disk space could not be measured.",
            "Check free space in Windows Settings before creating backups, quarantine sessions, or packages.")
    else:
        disk_mb = disk_free_bytes // (1024 * 1024)
        if disk_mb < LOW_DISK_WARN_MB:
            add("disk_space", "warn", f"Only {disk_mb} MiB free space is available.",
                "Free space safely before creating archives or backups; do not delete files merely to satisfy this check.")
        else:
            add("disk_space", "pass", f"Approximately {disk_mb} MiB free disk space is available.")

    if checker("git"):
        add("git", "pass", "Git executable is discoverable.")
    else:
        add("git", "warn", "Git is not on PATH; repository inspection and local PC-care commands can still work.",
            "Install Git for Windows only if you need Git workspaces or repository operations; no installation was attempted.")

    if system == "Windows":
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if powershell.is_file():
            add("windows_powershell", "pass", "The standard Windows PowerShell executable is present.")
        else:
            add("windows_powershell", "warn", "Windows PowerShell was not found at its standard path; driver and disk-status inventory may be unavailable.",
                "Check the Windows installation and system path; do not download replacement scripts or run repair commands automatically.")
    else:
        add("windows_features", "info", "Windows-only PC inventory commands will report unsupported on this platform.")

    failures = sum(check["status"] == "fail" for check in checks)
    warnings = sum(check["status"] == "warn" for check in checks)
    next_actions = [check["next_action"] for check in checks if check.get("next_action")]
    return {
        "schema_version": 1,
        "status": "blocked" if failures else ("caution" if warnings else "ready"),
        "ready": failures == 0,
        "read_only": True,
        "network_used": False,
        "ai_enabled": False,
        "project_root": str(root),
        "python_version": f"{version[0]}.{version[1]}",
        "available_memory_mb": None if memory is None or memory <= 0 else memory // (1024 * 1024),
        "disk_free_mb": None if disk_free_bytes is None or disk_free_bytes < 0 else disk_free_bytes // (1024 * 1024),
        "checks": checks,
        "next_actions": next_actions or ["No setup blocker detected. Run only the command you intend to use; destructive or write actions still require their own explicit confirmation."],
        "operations_launched": 0,
        "files_written": 0,
        "settings_changed": False,
    }
