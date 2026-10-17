"""Local PC-care inventory with explicitly confirmed, reversible temp-file quarantine."""
from __future__ import annotations

import csv
import heapq
import io
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .system_profile import get_system_profile

STALE_DAYS = 7
MAX_SCAN_ENTRIES = 5_000
MAX_SCAN_DEPTH = 5
REPARSE_POINT_ATTRIBUTE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT on Windows


def _is_link_or_reparse(path: Path) -> bool:
    """Avoid traversing symlinks, junctions, and other Windows reparse points."""
    try:
        info = path.stat(follow_symlinks=False)
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)
    except OSError:
        return True


def scan_stale_files(
    root: str | Path,
    *,
    now: float | None = None,
    minimum_age_days: int = STALE_DAYS,
    max_entries: int = MAX_SCAN_ENTRIES,
    max_depth: int = MAX_SCAN_DEPTH,
) -> dict:
    """Estimate old files in one cache/temp directory without reading or deleting them."""
    target = Path(root).expanduser()
    result = {
        "location": str(target),
        "status": "not_present",
        "entries_examined": 0,
        "candidate_files": 0,
        "candidate_bytes": 0,
        "minimum_age_days": minimum_age_days,
        "scan_limited": False,
        "access_errors": 0,
    }
    if not isinstance(minimum_age_days, int) or isinstance(minimum_age_days, bool) or not 1 <= minimum_age_days <= 365:
        raise ValueError("minimum_age_days must be between 1 and 365")
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= MAX_SCAN_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_SCAN_ENTRIES}")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= MAX_SCAN_DEPTH:
        raise ValueError(f"max_depth must be between 0 and {MAX_SCAN_DEPTH}")
    try:
        if not target.is_dir() or _is_link_or_reparse(target):
            return result
    except OSError:
        result["status"] = "access_limited"
        result["access_errors"] = 1
        return result

    now_value = time.time() if now is None else float(now)
    cutoff = now_value - minimum_age_days * 24 * 60 * 60
    stack: list[tuple[Path, int]] = [(target, 0)]
    limited = False

    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if result["entries_examined"] >= max_entries:
                        limited = True
                        break
                    result["entries_examined"] += 1
                    try:
                        entry_path = Path(entry.path)
                        if entry.is_symlink() or _is_link_or_reparse(entry_path):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                stack.append((entry_path, depth + 1))
                            else:
                                limited = True
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if info.st_mtime <= cutoff:
                            result["candidate_files"] += 1
                            result["candidate_bytes"] += max(0, int(info.st_size))
                    except OSError:
                        result["access_errors"] += 1
        except OSError:
            result["access_errors"] += 1
        if limited:
            break

    result["scan_limited"] = limited
    result["status"] = "scan_partial" if limited or result["access_errors"] else "scan_complete"
    return result


def default_scan_roots(env: dict[str, str] | None = None) -> list[tuple[str, Path]]:
    """Known per-user temp/cache locations; no roaming profiles or arbitrary directories."""
    environ = os.environ if env is None else env
    local = environ.get("LOCALAPPDATA")
    candidates: list[tuple[str, str | None]] = [
        ("temporary files", environ.get("TEMP") or environ.get("TMP")),
        ("local temporary files", str(Path(local) / "Temp") if local else None),
        ("Windows internet cache", str(Path(local) / "Microsoft/Windows/INetCache") if local else None),
        ("Chrome default-profile cache", str(Path(local) / "Google/Chrome/User Data/Default/Cache") if local else None),
        ("Edge default-profile cache", str(Path(local) / "Microsoft/Edge/User Data/Default/Cache") if local else None),
        ("Brave default-profile cache", str(Path(local) / "BraveSoftware/Brave-Browser/User Data/Default/Cache") if local else None),
    ]
    roots: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, raw_path in candidates:
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        try:
            key = os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            key = os.path.normcase(os.path.abspath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir() and not _is_link_or_reparse(path):
            roots.append((label, path))
    return roots



def _is_conventional_temp_path(
    path: str | Path,
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
) -> bool:
    """Allow execution only in the canonical per-user Windows Temp directory."""
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return False
    environ = os.environ if env is None else env
    local_app_data = environ.get("LOCALAPPDATA")
    if not local_app_data:
        return False
    try:
        actual = Path(path).expanduser().resolve(strict=True)
        expected = (Path(local_app_data).expanduser() / "Temp").resolve(strict=True)
        if not actual.is_dir() or not expected.is_dir():
            return False
        if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
            return False
        # Reject junctions/symlinks in ancestors as well as at the root itself.
        return _no_link_components(actual)
    except (OSError, RuntimeError, ValueError):
        return False


def read_startup_entries(*, windows: bool | None = None) -> dict:
    """List startup registry value names and Startup-folder filenames, never commands."""
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return {
            "status": "unsupported_platform",
            "items": [],
            "items_count": 0,
            "note": "Windows startup inventory is only available on Windows.",
        }

    items: list[dict] = []
    try:
        import winreg
    except ImportError:
        return {"status": "unavailable", "items": [], "items_count": 0}

    registry_locations = (
        (winreg.HKEY_CURRENT_USER, "HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_CURRENT_USER, "HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    )
    for hive, hive_name, subkey in registry_locations:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                count = min(winreg.QueryInfoKey(key)[1], 200 - len(items))
                for index in range(max(0, count)):
                    try:
                        name, _value, _kind = winreg.EnumValue(key, index)
                        items.append({"name": str(name)[:160], "source": f"{hive_name}\\{subkey}"})
                    except OSError:
                        break
            if len(items) >= 200:
                break
        except OSError:
            continue

    appdata = os.environ.get("APPDATA")
    program_data = os.environ.get("PROGRAMDATA")
    startup_dirs = (
        ("current-user startup folder", Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup" if appdata else None),
        ("all-users startup folder", Path(program_data) / "Microsoft/Windows/Start Menu/Programs/StartUp" if program_data else None),
    )
    for label, folder in startup_dirs:
        if folder is None or len(items) >= 200:
            continue
        try:
            if not folder.is_dir() or _is_link_or_reparse(folder):
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if len(items) >= 200:
                        break
                    try:
                        if entry.is_symlink() or _is_link_or_reparse(Path(entry.path)):
                            continue
                        items.append({"name": entry.name[:160], "source": label})
                    except OSError:
                        continue
        except OSError:
            continue

    return {
        "status": "collected",
        "items": items,
        "items_count": len(items),
        "note": "A startup entry is not proof of slowdown; review entries individually before changing them.",
    }


def read_top_processes(*, windows: bool | None = None, limit: int = 20) -> dict:
    """Read a bounded tasklist snapshot; never reads command lines or stops processes."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if not is_windows:
        return {
            "status": "unsupported_platform",
            "items": [],
            "items_count": 0,
            "note": "Process inventory is only available on Windows in this phase.",
        }

    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "unavailable",
            "items": [],
            "items_count": 0,
            "note": f"tasklist could not be read ({type(exc).__name__}); no processes were changed.",
        }

    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "items": [],
            "items_count": 0,
            "note": "Windows tasklist returned an error; no processes were changed.",
        }

    items: list[dict] = []
    try:
        rows = csv.reader(io.StringIO(completed.stdout))
        for row in rows:
            if len(row) < 5:
                continue
            try:
                pid = int(row[1].replace(",", "").strip())
                memory_text = row[4].strip().replace(",", "").replace(" K", "").replace(" KB", "")
                memory_kib = int(memory_text)
            except (ValueError, IndexError):
                continue
            items.append({
                "name": row[0][:160],
                "pid": pid,
                "session_name": row[2][:80],
                "session_number": row[3][:40],
                "memory_kib": max(0, memory_kib),
            })
    except csv.Error:
        return {
            "status": "parse_error",
            "items": [],
            "items_count": 0,
            "note": "The tasklist output could not be parsed safely.",
        }

    items.sort(key=lambda item: item["memory_kib"], reverse=True)
    selected = items[:limit]
    return {
        "status": "collected",
        "items": selected,
        "items_count": len(items),
        "returned_count": len(selected),
        "limit": limit,
        "note": "Snapshot only: memory use changes over time; no process was stopped or modified.",
    }


def read_installed_applications(*, windows: bool | None = None, limit: int = 500) -> dict:
    """Read installed-application metadata only; never read or invoke uninstall commands."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if not is_windows:
        return {
            "status": "unsupported_platform",
            "items": [],
            "items_count": 0,
            "note": "Installed application inventory is only available on Windows.",
        }
    try:
        import winreg
    except ImportError:
        return {"status": "unavailable", "items": [], "items_count": 0}

    uninstall_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    hives = (
        (winreg.HKEY_CURRENT_USER, "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    )
    views = []
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        views.append(winreg.KEY_WOW64_64KEY)
    if hasattr(winreg, "KEY_WOW64_32KEY"):
        views.append(winreg.KEY_WOW64_32KEY)
    if not views:
        views = [0]

    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for hive, hive_name in hives:
        for view in views:
            try:
                with winreg.OpenKey(hive, uninstall_path, 0, winreg.KEY_READ | view) as root:
                    subkey_count = min(winreg.QueryInfoKey(root)[0], 2_000)
                    for index in range(subkey_count):
                        if len(items) >= limit:
                            return {
                                "status": "partial_limit",
                                "items": items,
                                "items_count": len(items),
                                "limit": limit,
                                "note": "Inventory reached its cap; results may be incomplete.",
                            }
                        try:
                            subkey_name = winreg.EnumKey(root, index)
                            with winreg.OpenKey(root, subkey_name, 0, winreg.KEY_READ | view) as app_key:
                                def read_value(name: str) -> str:
                                    try:
                                        value, _kind = winreg.QueryValueEx(app_key, name)
                                    except OSError:
                                        return ""
                                    return value.strip()[:240] if isinstance(value, str) else ""

                                name = read_value("DisplayName")
                                if not name:
                                    continue
                                publisher = read_value("Publisher")
                                version = read_value("DisplayVersion")
                                install_date = read_value("InstallDate")
                                identity = (name.casefold(), publisher.casefold(), version.casefold())
                                if identity in seen:
                                    continue
                                seen.add(identity)
                                items.append({
                                    "name": name,
                                    "publisher": publisher or "Unknown",
                                    "version": version or "Unknown",
                                    "install_date": install_date or "Unknown",
                                    "registry_scope": hive_name,
                                })
                        except OSError:
                            continue
            except OSError:
                continue

    items.sort(key=lambda item: (item["name"].casefold(), item["publisher"].casefold()))
    return {
        "status": "collected",
        "items": items,
        "items_count": len(items),
        "limit": limit,
        "note": "Inventory metadata only. No uninstall commands, install locations, or command lines were read or executed.",
    }



WINGET_DEFAULT_SOURCE_URL = "https://cdn.winget.microsoft.com/cache"
WINGET_DEFAULT_SOURCE_NAME = "winget"
WINGET_SOURCE_EXPORT_TIMEOUT_SECONDS = 10
WINGET_CHECK_TIMEOUT_SECONDS = 30
WINGET_OUTPUT_MAX_CHARS = 12_000
WINGET_OUTPUT_MAX_LINES = 100


def _bounded_cli_lines(stdout: str, stderr: str) -> tuple[list[str], bool]:
    """Return a small readable excerpt from a provider's console output."""
    combined = "\n".join(part for part in (stdout, stderr) if part).replace("\r", "\n")
    combined = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", combined)
    lines = []
    truncated = len(combined) > WINGET_OUTPUT_MAX_CHARS
    remaining = WINGET_OUTPUT_MAX_CHARS
    for raw_line in combined.splitlines():
        line = "".join(character for character in raw_line if character.isprintable() or character == "\t").strip()
        if not line:
            continue
        if len(line) > 500:
            line = line[:500]
            truncated = True
        if len(line) > remaining:
            truncated = True
            break
        lines.append(line)
        remaining -= len(line) + 1
        if len(lines) >= WINGET_OUTPUT_MAX_LINES:
            truncated = True
            break
    return lines, truncated


def check_application_updates(*, windows: bool | None = None) -> dict:
    """Explicitly query the default trusted WinGet source; this function never installs packages."""
    is_windows = os.name == "nt" if windows is None else windows
    base_report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "source": WINGET_DEFAULT_SOURCE_NAME,
        "updates_installed": 0,
        "software_changes_made": False,
        "drivers_checked": False,
        "network_access_requested": False,
        "output_lines": [],
        "output_truncated": False,
        "limitations": [
            "This is an opt-in application-update listing only; it does not install or upgrade anything.",
            "WinGet's source is restricted to the default Microsoft HTTPS endpoint and must report trusted status.",
            "The provider's output is localized display text; BOTPYTON does not infer a definitive count from table formatting.",
            "Drivers, Windows system updates, and Microsoft Store updates are not checked by this phase.",
        ],
    }
    if not is_windows:
        return {**base_report, "status": "unsupported_platform", "note": "WinGet update checks are available only on Windows."}

    executable = shutil.which("winget")
    local_app_data = os.environ.get("LOCALAPPDATA")
    expected_alias = (Path(local_app_data) / "Microsoft" / "WindowsApps" / "winget.exe") if local_app_data else None
    if not executable or expected_alias is None:
        return {**base_report, "status": "unavailable", "note": "The expected Windows WinGet app-execution alias was not found."}
    try:
        actual_executable = os.path.normcase(os.path.abspath(executable))
        expected_executable = os.path.normcase(os.path.abspath(str(expected_alias)))
    except (OSError, ValueError):
        return {**base_report, "status": "untrusted_executable", "note": "WinGet executable path could not be validated."}
    if actual_executable != expected_executable:
        return {**base_report, "status": "untrusted_executable", "note": "The WinGet command resolved outside the expected per-user WindowsApps alias; no source was queried."}

    try:
        source_result = subprocess.run(
            [executable, "source", "export", WINGET_DEFAULT_SOURCE_NAME],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WINGET_SOURCE_EXPORT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {**base_report, "status": "source_check_timeout", "note": "WinGet source verification timed out; no update listing was requested."}
    except OSError as exc:
        return {**base_report, "status": "unavailable", "note": f"WinGet source verification failed ({type(exc).__name__}); no update listing was requested."}

    try:
        source_info = json.loads(source_result.stdout) if source_result.returncode == 0 else None
    except (TypeError, json.JSONDecodeError):
        source_info = None
    trust_values = source_info.get("TrustLevel", []) if isinstance(source_info, dict) else []
    source_is_trusted = (
        isinstance(source_info, dict)
        and source_info.get("Name") == WINGET_DEFAULT_SOURCE_NAME
        and str(source_info.get("Arg", "")).rstrip("/") == WINGET_DEFAULT_SOURCE_URL
        and source_info.get("Type") == "Microsoft.PreIndexed.Package"
        and isinstance(trust_values, list)
        and any(str(value).casefold() == "trusted" for value in trust_values)
    )
    if not source_is_trusted:
        return {
            **base_report,
            "status": "source_untrusted",
            "note": "The configured WinGet source did not match the expected Microsoft HTTPS source and trusted metadata. No package list was queried.",
        }

    command = [
        executable, "list", "--upgrade-available", "--source", WINGET_DEFAULT_SOURCE_NAME,
        "--disable-interactivity",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WINGET_CHECK_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        lines, truncated = _bounded_cli_lines(stdout, stderr)
        return {
            **base_report,
            "status": "timeout",
            "network_access_requested": True,
            "output_lines": lines,
            "output_truncated": truncated,
            "note": f"WinGet update listing exceeded {WINGET_CHECK_TIMEOUT_SECONDS} seconds; no installer was run.",
        }
    except OSError as exc:
        return {
            **base_report,
            "status": "command_unavailable",
            "network_access_requested": True,
            "note": f"WinGet update listing could not start ({type(exc).__name__}); no installer was run.",
        }

    lines, truncated = _bounded_cli_lines(result.stdout, result.stderr)
    return {
        **base_report,
        "status": "completed" if result.returncode == 0 else "source_or_query_error",
        "return_code": int(result.returncode),
        "network_access_requested": True,
        "output_lines": lines,
        "output_truncated": truncated,
        "note": "Listing only: command is fixed to 'winget list --upgrade-available --source winget --disable-interactivity'. It cannot invoke an install or upgrade operation.",
    }


def get_cleanup_preview(
    *,
    scan_roots: list[tuple[str, Path]] | None = None,
    now: float | None = None,
    minimum_age_days: int = STALE_DAYS,
    max_entries_per_root: int = MAX_SCAN_ENTRIES,
    max_candidates: int = 200,
) -> dict:
    """List exact old temporary-file candidates without reading or deleting file contents."""
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= 500:
        raise ValueError("max_candidates must be between 1 and 500")
    if not isinstance(minimum_age_days, int) or isinstance(minimum_age_days, bool) or not 1 <= minimum_age_days <= 365:
        raise ValueError("minimum_age_days must be between 1 and 365")
    roots = default_scan_roots() if scan_roots is None else scan_roots
    candidates: list[dict] = []
    skipped_roots: list[dict] = []
    entries_examined = 0
    partial = False
    now_value = time.time() if now is None else float(now)
    cutoff = now_value - minimum_age_days * 24 * 60 * 60

    for label, root_value in roots[:32]:
        # Only conventional temporary directories are eligible in this phase.
        normalized_label = label.casefold()
        if "temp" not in normalized_label and "temporary" not in normalized_label:
            skipped_roots.append({"label": label, "reason": "not_an_approved_temporary_directory"})
            continue
        root = Path(root_value).expanduser()
        if scan_roots is None and not _is_conventional_temp_path(root):
            skipped_roots.append({"label": label, "reason": "not_a_conventional_user_temp_path"})
            continue
        try:
            if not root.is_dir() or _is_link_or_reparse(root):
                skipped_roots.append({"label": label, "reason": "missing_or_linked_root"})
                continue
            resolved_root = root.resolve(strict=True)
        except OSError:
            skipped_roots.append({"label": label, "reason": "root_unavailable"})
            continue

        stack: list[tuple[Path, int]] = [(resolved_root, 0)]
        root_limit_hit = False
        root_entries_examined = 0

        while stack and len(candidates) < max_candidates:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if root_entries_examined >= max_entries_per_root:
                            partial = True
                            root_limit_hit = True
                            break
                        root_entries_examined += 1
                        entries_examined += 1
                        try:
                            entry_path = Path(entry.path)
                            if entry.is_symlink() or _is_link_or_reparse(entry_path):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth < MAX_SCAN_DEPTH:
                                    stack.append((entry_path, depth + 1))
                                else:
                                    partial = True
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            info = entry.stat(follow_symlinks=False)
                            if info.st_mtime > cutoff:
                                continue
                            resolved_file = entry_path.resolve(strict=True)
                            if not resolved_file.is_relative_to(resolved_root):
                                continue
                            candidates.append({
                                "path": str(resolved_file),
                                "root_label": label,
                                "size_bytes": max(0, int(info.st_size)),
                                "modified_epoch": int(info.st_mtime),
                                "modified_ns": int(info.st_mtime_ns),
                                "age_days": max(0, int((now_value - info.st_mtime) // 86400)),
                                "status": "candidate_not_deleted",
                                "reason": f"temporary file older than {minimum_age_days} days",
                            })
                            if len(candidates) >= max_candidates:
                                partial = True
                                break
                        except OSError:
                            partial = True
                    if root_limit_hit or len(candidates) >= max_candidates:
                        break
            except OSError:
                partial = True
            if root_limit_hit or len(candidates) >= max_candidates:
                break

    total_bytes = sum(item["size_bytes"] for item in candidates)
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "preview_partial" if partial else "preview_complete",
        "read_only": True,
        "network_used": False,
        "files_deleted": 0,
        "settings_changed": False,
        "minimum_age_days": minimum_age_days,
        "candidate_count": len(candidates),
        "candidate_bytes": total_bytes,
        "candidate_mib": round(total_bytes / (1024 ** 2), 2),
        "candidate_limit": max_candidates,
        "entries_examined": entries_examined,
        "candidates": candidates,
        "skipped_roots": skipped_roots,
        "limitations": [
            "This is a preview only. No files are deleted, moved, or opened for content.",
            "Only conventional temporary directories are eligible; browser caches, cookies, sessions, downloads, documents, and system directories are excluded.",
            "A file may be in use even if its timestamp is old. No cleanup action is authorized by this report.",
            "Partial results are not exhaustive; rerun after reviewing the listed paths.",
        ],
    }




MAX_STORAGE_SCAN_ENTRIES = 20_000
MAX_STORAGE_SCAN_DEPTH = 8
MAX_STORAGE_TOP_FILES = 50
MAX_STORAGE_EXTENSION_BUCKETS = 128
MAX_STORAGE_TIMEOUT_SECONDS = 10.0


def analyze_storage_path(
    root: str | Path,
    *,
    max_entries: int = 5_000,
    max_depth: int = 6,
    top_n: int = 20,
    timeout_seconds: float = 5.0,
) -> dict:
    """Summarize storage usage under one explicit folder; no file contents are read or changed."""
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= MAX_STORAGE_SCAN_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_STORAGE_SCAN_ENTRIES}")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= MAX_STORAGE_SCAN_DEPTH:
        raise ValueError(f"max_depth must be between 0 and {MAX_STORAGE_SCAN_DEPTH}")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= MAX_STORAGE_TOP_FILES:
        raise ValueError(f"top_n must be between 1 and {MAX_STORAGE_TOP_FILES}")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not 0.1 <= float(timeout_seconds) <= MAX_STORAGE_TIMEOUT_SECONDS):
        raise ValueError(f"timeout_seconds must be between 0.1 and {MAX_STORAGE_TIMEOUT_SECONDS}")

    target = Path(root).expanduser()
    started = time.perf_counter()
    report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "not_present",
        "root_path": str(target),
        "read_only": True,
        "network_used": False,
        "file_contents_read": False,
        "files_deleted": 0,
        "settings_changed": False,
        "entries_examined": 0,
        "directories_examined": 0,
        "files_examined": 0,
        "file_bytes_examined": 0,
        "largest_files": [],
        "extensions": [],
        "extension_types_returned": 0,
        "skipped_links": 0,
        "access_errors": 0,
        "scan_limited": False,
        "timeout_hit": False,
        "max_entries": max_entries,
        "max_depth": max_depth,
        "timeout_seconds": float(timeout_seconds),
        "elapsed_ms": 0.0,
        "limitations": [
            "Only metadata is inspected; file contents are never opened.",
            "The scan is bounded by entry count, recursion depth, result count and elapsed-time checks; partial totals are not a full-disk measurement.",
            "Symbolic links and Windows reparse points are skipped to avoid scanning outside the selected folder.",
            "A reported large file is not necessarily safe to delete. No deletion recommendation or automatic cleanup is performed.",
            "Reports include local paths. Review them before sharing.",
        ],
    }
    try:
        if not target.exists():
            report["status"] = "not_present"
            return report
        if _is_link_or_reparse(target):
            report["status"] = "unsafe_root"
            return report
        if not target.is_dir():
            report["status"] = "not_a_directory"
            return report
        root_path = target.resolve(strict=True)
        if not root_path.is_dir() or _is_link_or_reparse(root_path):
            report["status"] = "unsafe_root"
            return report
    except (OSError, RuntimeError, ValueError):
        report["status"] = "access_limited"
        report["access_errors"] = 1
        return report

    report["root_path"] = str(root_path)
    stack: list[tuple[Path, int]] = [(root_path, 0)]
    top_heap: list[tuple[int, str, str, int, str]] = []
    extension_buckets: dict[str, dict[str, int]] = {}
    limited = False
    deadline = started + float(timeout_seconds)

    while stack:
        if time.perf_counter() >= deadline:
            limited = True
            report["timeout_hit"] = True
            break
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if time.perf_counter() >= deadline:
                        limited = True
                        report["timeout_hit"] = True
                        break
                    if report["entries_examined"] >= max_entries:
                        limited = True
                        break
                    report["entries_examined"] += 1
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_symlink() or _is_link_or_reparse(entry_path):
                            report["skipped_links"] += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            report["directories_examined"] += 1
                            if depth < max_depth:
                                if len(stack) < max_entries:
                                    stack.append((entry_path, depth + 1))
                                else:
                                    limited = True
                            else:
                                limited = True
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        size = max(0, int(info.st_size))
                        report["files_examined"] += 1
                        report["file_bytes_examined"] += size
                        suffix = Path(entry.name).suffix.casefold() or "(no extension)"
                        bucket = suffix
                        if bucket not in extension_buckets and len(extension_buckets) >= MAX_STORAGE_EXTENSION_BUCKETS:
                            bucket = "(other extensions)"
                        stats = extension_buckets.setdefault(bucket, {"files": 0, "bytes": 0})
                        stats["files"] += 1
                        stats["bytes"] += size
                        path_text = str(entry_path)
                        heap_item = (size, path_text.casefold(), path_text, int(info.st_mtime), suffix)
                        if len(top_heap) < top_n:
                            heapq.heappush(top_heap, heap_item)
                        elif heap_item[:2] > top_heap[0][:2]:
                            heapq.heapreplace(top_heap, heap_item)
                    except OSError:
                        report["access_errors"] += 1
        except OSError:
            report["access_errors"] += 1
        if limited:
            break

    report["largest_files"] = [
        {"path": item[2], "size_bytes": item[0], "modified_epoch": item[3], "extension": item[4]}
        for item in sorted(top_heap, key=lambda item: (-item[0], item[1]))
    ]
    report["extensions"] = [
        {"extension": extension, "files": values["files"], "bytes": values["bytes"]}
        for extension, values in sorted(extension_buckets.items(), key=lambda item: (-item[1]["bytes"], item[0]))[:20]
    ]
    report["extension_types_returned"] = len(report["extensions"])
    report["scan_limited"] = limited or bool(report["access_errors"])
    report["status"] = "scan_partial" if report["scan_limited"] else "scan_complete"
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return report



MAX_DUPLICATE_SCAN_ENTRIES = 20_000
MAX_DUPLICATE_SCAN_DEPTH = 8
MAX_DUPLICATE_GROUPS = 25
MAX_DUPLICATE_HASH_FILES = 2_000
MAX_DUPLICATE_FILE_BYTES = 32 * 1024 * 1024
MAX_DUPLICATE_HASH_BYTES = 256 * 1024 * 1024
MAX_DUPLICATE_PATH_CHARS = 1_024
DUPLICATE_HASH_CHUNK_BYTES = 256 * 1024



DRIVER_INVENTORY_TIMEOUT_SECONDS = 15
DRIVER_INVENTORY_LIMIT = 200
DRIVER_INVENTORY_STDOUT_MAX_BYTES = 500_000
DRIVER_INVENTORY_FIELD_MAX_CHARS = 240


def read_device_driver_inventory(*, windows: bool | None = None, limit: int = DRIVER_INVENTORY_LIMIT) -> dict:
    """Read a bounded local Plug-and-Play driver inventory; never rates, installs, or updates drivers."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= DRIVER_INVENTORY_LIMIT:
        raise ValueError(f"limit must be between 1 and {DRIVER_INVENTORY_LIMIT}")
    base_report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "source": "Win32_PnPSignedDriver via local Windows PowerShell",
        "status": "unavailable",
        "read_only": True,
        "network_used": False,
        "drivers_installed": 0,
        "drivers_updated": 0,
        "settings_changed": False,
        "items_count": 0,
        "items": [],
        "limit": limit,
        "limitations": [
            "This is a local metadata inventory only. It does not compare drivers to online vendor catalogs or determine that a driver is outdated.",
            "No driver package is downloaded, installed, removed, disabled, or changed.",
            "The query is bounded to a maximum of 201 returned records and 15 seconds.",
            "Some devices/providers may expose incomplete metadata; inventory coverage is not a guarantee that every driver component is represented.",
        ],
    }
    if not is_windows:
        return {**base_report, "status": "unsupported_platform", "note": "Device-driver inventory requires Windows."}

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return {**base_report, "status": "unavailable", "note": "The Windows system directory was not found."}
    executable = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    try:
        if not executable.is_file() or _is_link_or_reparse(executable):
            return {**base_report, "status": "unavailable", "note": "The standard Windows PowerShell executable was not found or did not pass path checks."}
        executable = executable.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return {**base_report, "status": "unavailable", "note": "The standard Windows PowerShell executable could not be validated."}

    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "$ErrorActionPreference = 'Stop'; "
        "function Get-BoundedText([object]$Value) { "
        "$Text = [string]$Value; if ($Text.Length -gt 240) { $Text = $Text.Substring(0,240) }; return $Text }; "
        "Get-CimInstance -ClassName Win32_PnPSignedDriver -ErrorAction Stop | "
        "Select-Object -First 201 DeviceName,DeviceClass,Manufacturer,DriverProviderName,DriverVersion,DriverDate,IsSigned,InfName | "
        "ForEach-Object { [PSCustomObject]@{ "
        "device_name = (Get-BoundedText -Value $_.DeviceName); "
        "device_class = (Get-BoundedText -Value $_.DeviceClass); "
        "manufacturer = (Get-BoundedText -Value $_.Manufacturer); "
        "driver_provider = (Get-BoundedText -Value $_.DriverProviderName); "
        "driver_version = (Get-BoundedText -Value $_.DriverVersion); "
        "driver_date = (Get-BoundedText -Value $_.DriverDate); "
        "is_signed = (Get-BoundedText -Value $_.IsSigned); "
        "inf_name = (Get-BoundedText -Value $_.InfName) } } | ConvertTo-Json -Compress -Depth 2"
    )
    try:
        result = subprocess.run(
            [
                str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DRIVER_INVENTORY_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {**base_report, "status": "timeout", "note": f"Local driver inventory exceeded {DRIVER_INVENTORY_TIMEOUT_SECONDS} seconds; no changes were made."}
    except OSError as exc:
        return {**base_report, "status": "query_unavailable", "note": f"Local driver inventory could not start ({type(exc).__name__}); no changes were made."}

    if result.returncode != 0:
        return {
            **base_report,
            "status": "query_error",
            "return_code": int(result.returncode),
            "note": "Windows returned an error for its local driver metadata query; no update or installation was attempted.",
        }
    output = result.stdout or ""
    if len(output.encode("utf-8", errors="replace")) > DRIVER_INVENTORY_STDOUT_MAX_BYTES:
        return {**base_report, "status": "output_too_large", "note": "Driver inventory exceeded the output safety cap; results were rejected."}
    try:
        decoded = json.loads(output) if output.strip() else []
    except (TypeError, json.JSONDecodeError):
        return {**base_report, "status": "invalid_output", "note": "Windows driver inventory output was not valid JSON; no data was inferred."}
    if decoded is None:
        records = []
    elif isinstance(decoded, dict):
        records = [decoded]
    elif isinstance(decoded, list):
        records = decoded
    else:
        return {**base_report, "status": "invalid_output", "note": "Windows driver inventory had an unexpected result shape."}
    if any(not isinstance(record, dict) for record in records):
        return {**base_report, "status": "invalid_output", "note": "Windows returned a driver record with an unexpected shape."}

    items = []
    for record in records[:limit]:
        def bounded_text(key: str) -> str:
            value = record.get(key, "")
            return value.strip()[:DRIVER_INVENTORY_FIELD_MAX_CHARS] if isinstance(value, str) else ""

        signed_raw = record.get("is_signed")
        if isinstance(signed_raw, bool):
            is_signed = signed_raw
        elif isinstance(signed_raw, str) and signed_raw.strip().casefold() in {"true", "false"}:
            is_signed = signed_raw.strip().casefold() == "true"
        else:
            is_signed = None
        items.append({
            "device_name": bounded_text("device_name"),
            "device_class": bounded_text("device_class"),
            "manufacturer": bounded_text("manufacturer"),
            "driver_provider": bounded_text("driver_provider"),
            "driver_version": bounded_text("driver_version"),
            "driver_date": bounded_text("driver_date"),
            "is_signed": is_signed,
            "inf_name": bounded_text("inf_name"),
        })
    capped = len(records) > limit
    return {
        **base_report,
        "status": "partial_limit" if capped else "collected",
        "items_count": len(items),
        "items": items,
        "provider_records_seen": min(len(records), DRIVER_INVENTORY_LIMIT + 1),
        "note": (
            "Inventory reached its cap; records may be incomplete."
            if capped else
            "Local Plug-and-Play driver metadata only. Driver date/version is not evidence of being outdated; no online comparison or driver change was performed."
        ),
    }



DISK_HEALTH_TIMEOUT_SECONDS = 15
DISK_HEALTH_LIMIT = 100
DISK_HEALTH_STDOUT_MAX_BYTES = 500_000
DISK_HEALTH_FIELD_MAX_CHARS = 200


def read_disk_health_inventory(*, windows: bool | None = None, limit: int = DISK_HEALTH_LIMIT) -> dict:
    """Read bounded local Windows disk and Storage-provider status metadata; no repair or changes."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= DISK_HEALTH_LIMIT:
        raise ValueError(f"limit must be between 1 and {DISK_HEALTH_LIMIT}")
    base_report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "source": "local Windows PowerShell: Get-PhysicalDisk and Win32_DiskDrive",
        "status": "unavailable",
        "read_only": True,
        "network_used": False,
        "disks_modified": 0,
        "settings_changed": False,
        "physical_health_available": False,
        "physical_disks_count": 0,
        "windows_disks_count": 0,
        "physical_disks": [],
        "windows_disks": [],
        "limit_per_provider": limit,
        "limitations": [
            "This is a local status snapshot, not a full S.M.A.R.T. attribute report or a guarantee that a disk is healthy.",
            "Windows Storage health data may be unavailable for USB bridges, RAID controllers, virtual disks, or some storage drivers.",
            "An empty or unavailable provider result does not prove that the disk is healthy; use the drive/vendor diagnostic when symptoms or warnings exist.",
            "No repair, scan-and-fix, formatting, optimization, driver update, or other disk modification is performed.",
            f"Each provider is capped at {limit} records; the command has a {DISK_HEALTH_TIMEOUT_SECONDS}-second timeout.",
        ],
    }
    if not is_windows:
        return {**base_report, "status": "unsupported_platform", "note": "Disk health inventory requires Windows."}

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return {**base_report, "status": "unavailable", "note": "The Windows system directory was not found."}
    executable = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    try:
        if not executable.is_file() or _is_link_or_reparse(executable):
            return {**base_report, "status": "unavailable", "note": "The standard Windows PowerShell executable was not found or did not pass path checks."}
        executable = executable.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return {**base_report, "status": "unavailable", "note": "The standard Windows PowerShell executable could not be validated."}

    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "$ErrorActionPreference = 'Stop'; "
        "$physical = @(); $physicalError = ''; "
        "try { $physical = @(Get-PhysicalDisk -ErrorAction Stop | Select-Object -First 101 "
        "@{Name='name';Expression={[string]$_.FriendlyName}}, "
        "@{Name='health_status';Expression={[string]$_.HealthStatus}}, "
        "@{Name='operational_status';Expression={(@($_.OperationalStatus | ForEach-Object { [string]$_ }) -join ',')}}, "
        "@{Name='media_type';Expression={[string]$_.MediaType}}, "
        "@{Name='bus_type';Expression={[string]$_.BusType}}, "
        "@{Name='size_bytes';Expression={[string]$_.Size}}) } "
        "catch { $physicalError = $_.Exception.GetType().Name }; "
        "$windowsDisks = @(Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction Stop | Select-Object -First 101 "
        "@{Name='name';Expression={[string]$_.Model}}, "
        "@{Name='status';Expression={[string]$_.Status}}, "
        "@{Name='media_type';Expression={[string]$_.MediaType}}, "
        "@{Name='interface_type';Expression={[string]$_.InterfaceType}}, "
        "@{Name='size_bytes';Expression={[string]$_.Size}}, "
        "@{Name='partitions';Expression={[string]$_.Partitions}}); "
        "[PSCustomObject]@{physical_disks=$physical; windows_disks=$windowsDisks; physical_query_error=$physicalError} | "
        "ConvertTo-Json -Compress -Depth 4"
    )
    try:
        result = subprocess.run(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DISK_HEALTH_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {**base_report, "status": "timeout", "note": f"Local disk health query exceeded {DISK_HEALTH_TIMEOUT_SECONDS} seconds; no changes were made."}
    except OSError as exc:
        return {**base_report, "status": "query_unavailable", "note": f"Local disk inventory could not start ({type(exc).__name__}); no changes were made."}

    if result.returncode != 0:
        return {
            **base_report, "status": "query_error", "return_code": int(result.returncode),
            "note": "Windows could not complete the read-only disk inventory; no repair or change was attempted.",
        }
    output = result.stdout or ""
    if len(output.encode("utf-8", errors="replace")) > DISK_HEALTH_STDOUT_MAX_BYTES:
        return {**base_report, "status": "output_too_large", "note": "Disk inventory exceeded the output safety cap; results were rejected."}
    try:
        decoded = json.loads(output) if output.strip() else None
    except (TypeError, json.JSONDecodeError):
        return {**base_report, "status": "invalid_output", "note": "Windows disk inventory output was not valid JSON; no status was inferred."}
    if not isinstance(decoded, dict):
        return {**base_report, "status": "invalid_output", "note": "Windows disk inventory had an unexpected result shape."}
    raw_physical = decoded.get("physical_disks", [])
    raw_windows = decoded.get("windows_disks", [])
    if raw_physical is None:
        raw_physical = []
    if raw_windows is None:
        raw_windows = []
    if isinstance(raw_physical, dict):
        raw_physical = [raw_physical]
    if isinstance(raw_windows, dict):
        raw_windows = [raw_windows]
    if not isinstance(raw_physical, list) or not isinstance(raw_windows, list):
        return {**base_report, "status": "invalid_output", "note": "Windows disk inventory returned an unexpected collection shape."}
    if any(not isinstance(row, dict) for row in raw_physical + raw_windows):
        return {**base_report, "status": "invalid_output", "note": "Windows disk inventory returned a record with an unexpected shape."}

    def safe_text(record: dict, key: str) -> str:
        value = record.get(key, "")
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return str(value).strip()[:DISK_HEALTH_FIELD_MAX_CHARS]
        return ""

    def safe_size(record: dict) -> int | None:
        value = safe_text(record, "size_bytes")
        if not value or not value.isdecimal():
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if 0 <= parsed <= 2**63 - 1 else None

    def safe_count(record: dict, key: str) -> int | None:
        value = safe_text(record, key)
        if not value or not value.isdecimal():
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if 0 <= parsed <= 1_000_000 else None

    physical = []
    for record in raw_physical[:limit]:
        physical.append({
            "name": safe_text(record, "name"),
            "health_status": safe_text(record, "health_status") or "Unknown",
            "operational_status": safe_text(record, "operational_status") or "Unknown",
            "media_type": safe_text(record, "media_type") or "Unknown",
            "bus_type": safe_text(record, "bus_type") or "Unknown",
            "size_bytes": safe_size(record),
        })
    windows_rows = []
    for record in raw_windows[:limit]:
        windows_rows.append({
            "name": safe_text(record, "name"),
            "reported_status": safe_text(record, "status") or "Unknown",
            "media_type": safe_text(record, "media_type") or "Unknown",
            "interface_type": safe_text(record, "interface_type") or "Unknown",
            "size_bytes": safe_size(record),
            "partitions": safe_count(record, "partitions"),
        })
    physical_capped = len(raw_physical) > limit
    windows_capped = len(raw_windows) > limit
    physical_error = safe_text(decoded, "physical_query_error")
    has_provider_data = bool(physical or windows_rows)
    status = "partial_limit" if physical_capped or windows_capped else ("collected" if has_provider_data else "empty_inventory")
    note = (
        "A provider reached its result cap; inventory may be incomplete."
        if status == "partial_limit" else
        "No disk records were returned. This is not evidence that storage is healthy."
        if not has_provider_data else
        "Read-only status snapshot. Windows Storage health is not a full S.M.A.R.T. diagnosis; a blank or OK status is not a health guarantee."
    )
    if physical_error:
        note += f" Get-PhysicalDisk was unavailable ({physical_error}); Windows disk metadata may still be present."
    return {
        **base_report,
        "status": status,
        "physical_health_available": bool(physical),
        "physical_disks_count": len(physical),
        "windows_disks_count": len(windows_rows),
        "physical_disks": physical,
        "windows_disks": windows_rows,
        "provider_records_seen": {
            "physical_disks": min(len(raw_physical), DISK_HEALTH_LIMIT + 1),
            "windows_disks": min(len(raw_windows), DISK_HEALTH_LIMIT + 1),
        },
        "note": note,
    }


def find_duplicate_files(
    root: str | Path,
    *,
    max_entries: int = 5_000,
    max_depth: int = 6,
    timeout_seconds: float = 5.0,
    max_file_bytes: int = MAX_DUPLICATE_FILE_BYTES,
    max_hash_bytes: int = MAX_DUPLICATE_HASH_BYTES,
    max_groups: int = 20,
) -> dict:
    """Find likely exact duplicate files in an explicitly selected folder; never deletes files."""
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= MAX_DUPLICATE_SCAN_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_DUPLICATE_SCAN_ENTRIES}")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= MAX_DUPLICATE_SCAN_DEPTH:
        raise ValueError(f"max_depth must be between 0 and {MAX_DUPLICATE_SCAN_DEPTH}")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not 0.1 <= float(timeout_seconds) <= MAX_STORAGE_TIMEOUT_SECONDS):
        raise ValueError(f"timeout_seconds must be between 0.1 and {MAX_STORAGE_TIMEOUT_SECONDS}")
    if (not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool)
            or not 1 <= max_file_bytes <= MAX_DUPLICATE_FILE_BYTES):
        raise ValueError(f"max_file_bytes must be between 1 and {MAX_DUPLICATE_FILE_BYTES}")
    if (not isinstance(max_hash_bytes, int) or isinstance(max_hash_bytes, bool)
            or not 1 <= max_hash_bytes <= MAX_DUPLICATE_HASH_BYTES):
        raise ValueError(f"max_hash_bytes must be between 1 and {MAX_DUPLICATE_HASH_BYTES}")
    if not isinstance(max_groups, int) or isinstance(max_groups, bool) or not 1 <= max_groups <= MAX_DUPLICATE_GROUPS:
        raise ValueError(f"max_groups must be between 1 and {MAX_DUPLICATE_GROUPS}")

    target = Path(root).expanduser()
    started = time.perf_counter()
    report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "not_present",
        "root_path": str(target),
        "read_only": True,
        "network_used": False,
        "file_contents_read_for_sha256": True,
        "files_deleted": 0,
        "settings_changed": False,
        "entries_examined": 0,
        "directories_examined": 0,
        "files_examined": 0,
        "same_size_candidate_files": 0,
        "files_hashed": 0,
        "hash_bytes_examined": 0,
        "oversize_candidate_files_skipped": 0,
        "hash_budget_skipped_files": 0,
        "changed_or_inaccessible_files": 0,
        "scan_diagnostics": [],
        "duplicate_group_count": 0,
        "potential_duplicate_bytes": 0,
        "duplicate_groups": [],
        "access_errors": 0,
        "skipped_links": 0,
        "scan_limited": False,
        "timeout_hit": False,
        "max_entries": max_entries,
        "max_depth": max_depth,
        "timeout_seconds": float(timeout_seconds),
        "max_file_bytes": max_file_bytes,
        "max_hash_bytes": max_hash_bytes,
        "max_groups": max_groups,
        "elapsed_ms": 0.0,
        "limitations": [
            "The selected files are read in bounded chunks to calculate SHA-256; file contents are not sent anywhere.",
            "Only same-size candidates are hashed. Files larger than the per-file cap are excluded, and the report may be partial when budgets are reached.",
            "Potential reclaimable bytes assume one copy could be kept; hard links, application use, permissions or file locks can make actual reclaim different.",
            "Duplicate detection is advisory only. BOTPYTON never deletes, moves or rewrites a candidate.",
            "Reports include local paths. Review them before sharing.",
        ],
    }
    try:
        if not target.exists():
            report["status"] = "not_present"
            return report
        if _is_link_or_reparse(target):
            report["status"] = "unsafe_root"
            return report
        if not target.is_dir():
            report["status"] = "not_a_directory"
            return report
        root_path = target.resolve(strict=True)
        if not root_path.is_dir() or _is_link_or_reparse(root_path):
            report["status"] = "unsafe_root"
            return report
    except (OSError, RuntimeError, ValueError):
        report["status"] = "access_limited"
        report["access_errors"] = 1
        return report
    report["root_path"] = str(root_path)

    deadline = started + float(timeout_seconds)
    stack: list[tuple[Path, int]] = [(root_path, 0)]
    metadata: list[dict] = []
    limited = False
    while stack:
        if time.perf_counter() >= deadline:
            limited = True
            report["timeout_hit"] = True
            break
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if time.perf_counter() >= deadline:
                        limited = True
                        report["timeout_hit"] = True
                        break
                    if report["entries_examined"] >= max_entries:
                        limited = True
                        break
                    report["entries_examined"] += 1
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_symlink() or _is_link_or_reparse(entry_path):
                            report["skipped_links"] += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            report["directories_examined"] += 1
                            if depth < max_depth and len(stack) < max_entries:
                                stack.append((entry_path, depth + 1))
                            else:
                                limited = True
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        report["files_examined"] += 1
                        if info.st_size < 0 or info.st_size > 2**63 - 1:
                            report["changed_or_inaccessible_files"] += 1
                            limited = True
                            continue
                        path_text = str(entry_path)
                        if len(path_text) > MAX_DUPLICATE_PATH_CHARS:
                            report["changed_or_inaccessible_files"] += 1
                            limited = True
                            continue
                        metadata.append({
                            "path": path_text,
                            "size_bytes": int(info.st_size),
                            "modified_ns": int(info.st_mtime_ns),
                        })
                    except OSError:
                        report["access_errors"] += 1
        except OSError:
            report["access_errors"] += 1
        if limited:
            break

    by_size: dict[int, list[dict]] = {}
    for item in metadata:
        by_size.setdefault(item["size_bytes"], []).append(item)
    candidate_buckets = [bucket for bucket in by_size.values() if len(bucket) > 1]
    candidate_buckets.sort(key=lambda bucket: (bucket[0]["size_bytes"], bucket[0]["path"].casefold()))
    report["same_size_candidate_files"] = sum(len(bucket) for bucket in candidate_buckets)

    hash_groups: dict[str, list[dict]] = {}
    hash_limit_hit = False
    for bucket_index, bucket in enumerate(candidate_buckets):
        if time.perf_counter() >= deadline:
            limited = True
            report["timeout_hit"] = True
            break
        size_bytes = bucket[0]["size_bytes"]
        if size_bytes > max_file_bytes:
            report["oversize_candidate_files_skipped"] += len(bucket)
            limited = True
            continue
        for member_index, item in enumerate(bucket):
            if time.perf_counter() >= deadline:
                limited = True
                report["timeout_hit"] = True
                hash_limit_hit = True
                break
            if report["files_hashed"] >= MAX_DUPLICATE_HASH_FILES:
                report["hash_budget_skipped_files"] += len(bucket) - member_index + sum(len(entries) for entries in candidate_buckets[bucket_index + 1:])
                limited = True
                hash_limit_hit = True
                break
            remaining = max_hash_bytes - report["hash_bytes_examined"]
            if size_bytes > remaining:
                report["hash_budget_skipped_files"] += len(bucket) - member_index + sum(len(entries) for entries in candidate_buckets[bucket_index + 1:])
                limited = True
                hash_limit_hit = True
                break
            candidate_path = Path(item["path"])
            try:
                if _is_link_or_reparse(candidate_path) or not _no_link_components(candidate_path, stop_at=root_path):
                    raise OSError("path includes a link or reparse point")
                resolved = candidate_path.resolve(strict=True)
                if not resolved.is_relative_to(root_path):
                    raise OSError("path moved outside selected folder")
                before = candidate_path.stat(follow_symlinks=False)
                if (not stat.S_ISREG(before.st_mode) or before.st_size != size_bytes
                        or before.st_mtime_ns != item["modified_ns"]):
                    raise OSError("file metadata changed during scan")
                digest = _sha256_file(candidate_path, byte_limit=min(size_bytes, remaining, max_file_bytes), deadline=deadline)
                after = candidate_path.stat(follow_symlinks=False)
                # Windows stat inode values are not consistent across all filesystem providers.
                # Size + nanosecond mtime are compared here and inside _sha256_file.
                if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                    raise OSError("file metadata changed during hashing")
            except TimeoutError:
                report["timeout_hit"] = True
                limited = True
                hash_limit_hit = True
                break
            except (OSError, RuntimeError, ValueError) as exc:
                report["changed_or_inaccessible_files"] += 1
                if len(report["scan_diagnostics"]) < 10:
                    report["scan_diagnostics"].append({
                        "file": item["path"],
                        "failure_type": type(exc).__name__,
                        "reason": str(exc)[:160],
                    })
                limited = True
                continue
            report["files_hashed"] += 1
            report["hash_bytes_examined"] += size_bytes
            hash_groups.setdefault(digest, []).append(item)
        if hash_limit_hit:
            break

    groups = []
    for digest, members in hash_groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: item["path"].casefold())
        file_size = members[0]["size_bytes"]
        potential_bytes = file_size * (len(members) - 1)
        groups.append({
            "sha256": digest,
            "size_bytes": file_size,
            "file_count": len(members),
            "potential_duplicate_bytes": potential_bytes,
            "paths_total": len(members),
            "paths": [item["path"] for item in members[:10]],
            "paths_truncated": len(members) > 10,
        })
    groups.sort(key=lambda item: (-item["potential_duplicate_bytes"], -item["file_count"], item["sha256"]))
    report["duplicate_group_count"] = len(groups)
    report["potential_duplicate_bytes"] = sum(item["potential_duplicate_bytes"] for item in groups)
    report["duplicate_groups"] = groups[:max_groups]
    report["scan_limited"] = limited or bool(report["access_errors"])
    report["status"] = "scan_partial" if report["scan_limited"] else "scan_complete"
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return report


def _process_memory_snapshot(*, windows: bool | None = None) -> dict:
    """Read current and lifetime peak resident memory for this process without dependencies."""
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
            if ok:
                return {
                    "status": "measured",
                    "source": "GetProcessMemoryInfo",
                    "current_rss_bytes": int(counters.WorkingSetSize),
                    "peak_rss_bytes": int(counters.PeakWorkingSetSize),
                }
        except (AttributeError, OSError, ImportError):
            pass
        return {"status": "unavailable", "source": "GetProcessMemoryInfo"}
    status_path = Path("/proc/self/status")
    if status_path.is_file():
        try:
            values = {}
            for line in status_path.read_text(encoding="ascii").splitlines():
                if line.startswith("VmRSS:") or line.startswith("VmHWM:"):
                    parts = line.split()
                    values[parts[0].rstrip(":")] = int(parts[1]) * 1024
            if "VmRSS" in values:
                return {
                    "status": "measured",
                    "source": "/proc/self/status",
                    "current_rss_bytes": values["VmRSS"],
                    "peak_rss_bytes": values.get("VmHWM"),
                }
        except (OSError, ValueError, IndexError):
            pass
    return {"status": "unavailable", "source": "not_supported"}


def get_pc_care_benchmark(
    *,
    windows: bool | None = None,
    scan_roots: list[tuple[str, Path]] | None = None,
) -> dict:
    """Run one bounded, read-only PC-care probe and report timings/RSS; never updates or cleans."""
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    memory_before = _process_memory_snapshot(windows=windows)
    operations: list[dict] = []
    measurements: dict[str, dict] = {}

    def measure(name: str, callback) -> None:
        wall = time.perf_counter()
        cpu = time.process_time()
        try:
            result = callback()
            operation_status = result.get("status", "completed") if isinstance(result, dict) else "completed"
            item_count = result.get("items_count", result.get("candidate_count", 0)) if isinstance(result, dict) else 0
            scanned = result.get("entries_examined", 0) if isinstance(result, dict) else 0
            error_type = None
        except (OSError, ValueError, RuntimeError) as exc:
            operation_status = "unavailable"
            item_count = 0
            scanned = 0
            error_type = type(exc).__name__
        timing = {
            "name": name,
            "status": operation_status,
            "elapsed_wall_ms": round((time.perf_counter() - wall) * 1000, 2),
            "elapsed_cpu_ms": round(max(0.0, time.process_time() - cpu) * 1000, 2),
            "item_count": max(0, int(item_count)) if isinstance(item_count, int) and not isinstance(item_count, bool) else 0,
            "entries_examined": max(0, int(scanned)) if isinstance(scanned, int) and not isinstance(scanned, bool) else 0,
        }
        if error_type:
            timing["failure_type"] = error_type
        operations.append(timing)
        measurements[name] = {
            "status": operation_status,
            "failure_type": error_type,
            "network_used": False,
        }

    measure("system_profile", lambda: get_system_profile())
    measure("startup_inventory", lambda: read_startup_entries(windows=windows))
    measure("process_memory_inventory", lambda: read_top_processes(windows=windows, limit=10))
    measure("application_inventory", lambda: read_installed_applications(windows=windows, limit=100))
    measure("cleanup_preview", lambda: get_cleanup_preview(
        scan_roots=scan_roots, max_entries_per_root=1_000, max_candidates=30
    ))
    memory_after = _process_memory_snapshot(windows=windows)
    elapsed_wall = max(0.0, time.perf_counter() - started_wall)
    elapsed_cpu = max(0.0, time.process_time() - started_cpu)
    measured = memory_before.get("status") == "measured" or memory_after.get("status") == "measured"
    peak_values = [
        value for value in (memory_before.get("peak_rss_bytes"), memory_after.get("peak_rss_bytes"))
        if isinstance(value, int)
    ]
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "measured" if measured else "partial_measurement",
        "read_only": True,
        "network_used": False,
        "ai_enabled": False,
        "files_deleted": 0,
        "files_quarantined": 0,
        "settings_changed": False,
        "software_changes_made": False,
        "drivers_updated": False,
        "measurements": {
            "total_elapsed_wall_ms": round(elapsed_wall * 1000, 2),
            "total_elapsed_cpu_ms": round(elapsed_cpu * 1000, 2),
            "process_memory_before": memory_before,
            "process_memory_after": memory_after,
            "process_peak_rss_bytes": max(peak_values) if peak_values else None,
            "operations": operations,
        },
        "operation_summaries": measurements,
        "limitations": [
            "One diagnostic pass only. Timings vary with background tasks, Windows state, disks, antivirus and permissions.",
            "Peak RSS is the process lifetime high-water mark when the OS exposes it; it is not an isolated peak attributable solely to this run.",
            "The probe runs a bounded preview and metadata inventories only. It never removes files, disables startup items, updates apps, installs drivers, or contacts a network service.",
            "This benchmark does not represent CCleaner performance and cannot replace a measurement on the target machine.",
        ],
    }



QUARANTINE_CONFIRMATION = "QUARANTINE OLD TEMP FILES"
RESTORE_CONFIRMATION = "RESTORE BOTPYTON QUARANTINE"
MAX_QUARANTINE_CANDIDATES = 100
MAX_QUARANTINE_HASH_BYTES = 512 * 1024 * 1024
HASH_CHUNK_BYTES = 256 * 1024
MAX_AUDIT_RECORDS = 200
MAX_AUDIT_SCAN_FILES = 5_000
MAX_AUDIT_TOTAL_BYTES = 2_000_000
MAX_AUDIT_RECORD_BYTES = 16_000
QUARANTINE_MANIFEST_SCHEMA = 1


def _is_temporary_root_label(label: str) -> bool:
    normalized = str(label).casefold()
    return "temp" in normalized or "temporary" in normalized


def _approved_temporary_roots(scan_roots: list[tuple[str, Path]] | None) -> dict[str, Path]:
    source_roots = default_scan_roots() if scan_roots is None else scan_roots
    approved: dict[str, Path] = {}
    for label, value in source_roots[:32]:
        if not isinstance(label, str) or not _is_temporary_root_label(label) or label in approved:
            continue
        root = Path(value).expanduser()
        if scan_roots is None and not _is_conventional_temp_path(root):
            continue
        try:
            if root.is_dir() and not _is_link_or_reparse(root):
                approved[label] = root.resolve(strict=True)
        except OSError:
            continue
    return approved


def _no_link_components(path: Path, *, stop_at: Path | None = None) -> bool:
    """Reject links/reparse points in the lexical path chain."""
    try:
        current = Path(os.path.abspath(str(path)))
        stop_text = os.path.normcase(str(Path(os.path.abspath(str(stop_at))))) if stop_at else None
        while True:
            if current.exists() or current.is_symlink():
                if _is_link_or_reparse(current):
                    return False
            current_text = os.path.normcase(str(current))
            if stop_text is not None and current_text == stop_text:
                return True
            parent = current.parent
            if parent == current:
                return stop_at is None
            if stop_text is not None:
                parent_text = os.path.normcase(str(parent))
                if stop_text != parent_text and stop_text not in {
                    os.path.normcase(str(item)) for item in parent.parents
                }:
                    return False
            current = parent
    except OSError:
        return False


def _safe_regular_file_info(path_value: str | Path, root: Path):
    """Return lstat data only for a regular file lexically under a trusted root."""
    import stat as stat_module

    try:
        path = Path(path_value)
        if not path.is_absolute() or ".." in path.parts or _is_link_or_reparse(path):
            return None
        root = root.resolve(strict=True)
        absolute_path = Path(os.path.abspath(str(path)))
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            return None
        if os.path.normcase(str(absolute_path)) != os.path.normcase(str(resolved)):
            return None
        if not _no_link_components(absolute_path, stop_at=root):
            return None
        info = path.stat(follow_symlinks=False)
        if not stat_module.S_ISREG(info.st_mode):
            return None
        return info
    except (OSError, RuntimeError, ValueError):
        return None


def _safe_restore_destination(path_value: str | Path, root: Path) -> Path | None:
    """Validate a missing destination beneath an existing approved temp root."""
    try:
        path = Path(path_value)
        if not path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".."}:
            return None
        if path.exists() or path.is_symlink():
            return None
        root = root.resolve(strict=True)
        parent = path.parent
        resolved_parent = parent.resolve(strict=True)
        if not resolved_parent.is_relative_to(root):
            return None
        if not _no_link_components(parent, stop_at=root):
            return None
        if os.path.normcase(str(Path(os.path.abspath(str(parent))))) != os.path.normcase(str(resolved_parent)):
            return None
        return resolved_parent / path.name
    except (OSError, RuntimeError, ValueError):
        return None


def _quarantine_manifest_digest(manifest: dict) -> str:
    """Return a deterministic checksum over the manifest excluding its checksum field."""
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_quarantine_manifest(manifest_path: Path, manifest: dict) -> None:
    """Atomically persist a checksummed recovery manifest before/after each move."""
    manifest["manifest_sha256"] = _quarantine_manifest_digest(manifest)
    temporary_path = manifest_path.with_name(f".manifest-{time.time_ns()}.tmp")
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_path, manifest_path)


def _default_quarantine_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data).expanduser()
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "BOTPYTON" / "Quarantine"


def _path_is_inside_any_root(path: Path, roots: dict[str, Path]) -> bool:
    try:
        resolved = path.resolve(strict=False)
        return any(resolved == root or resolved.is_relative_to(root) for root in roots.values())
    except (OSError, RuntimeError, ValueError):
        return True




def _move_no_replace(source: str | Path, destination: str | Path) -> None:
    """Move atomically without overwriting an existing destination on any platform."""
    source_path = Path(source)
    destination_path = Path(destination)
    if os.path.lexists(destination_path):
        raise FileExistsError("destination already exists")
    if os.name == "nt":
        # Windows os.rename fails when the destination already exists.
        os.rename(source_path, destination_path)
        return
    # POSIX rename/replace can overwrite; hard-link creation is atomic and fails on collisions.
    os.link(source_path, destination_path, follow_symlinks=False)
    try:
        os.unlink(source_path)
    except OSError:
        try:
            os.unlink(destination_path)
        except OSError:
            pass
        raise


def _sha256_file(path: str | Path, *, byte_limit: int, deadline: float | None = None) -> str:
    """Hash a regular file with bounded memory and detect concurrent modification."""
    target = Path(path)
    before = target.stat(follow_symlinks=False)
    if before.st_size < 0 or before.st_size > byte_limit:
        raise ValueError("file exceeds the configured integrity-hash byte budget")
    digest = hashlib.sha256()
    total = 0
    with target.open("rb") as stream:
        while True:
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError("file hashing deadline exceeded")
            chunk = stream.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > byte_limit:
                raise ValueError("file exceeded the integrity-hash byte budget while being read")
            digest.update(chunk)
    after = target.stat(follow_symlinks=False)
    if (total != before.st_size or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns):
        raise ValueError("file changed while its integrity hash was being calculated")
    return digest.hexdigest()


def _default_pc_care_audit_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data).expanduser()
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "BOTPYTON" / "PC-Care" / "audit"


def _persist_pc_care_audit_event(
    operation: str,
    phase: str,
    status: str,
    *,
    item_count: int = 0,
    succeeded_count: int = 0,
    skipped_count: int = 0,
    session_id: str | None = None,
    audit_dir: str | Path | None = None,
) -> dict:
    """Append a local, checksummed event without storing user file paths or contents."""
    allowed_operations = {"quarantine", "restore"}
    allowed_phases = {"started", "completed"}
    if operation not in allowed_operations or phase not in allowed_phases:
        raise ValueError("unsupported PC-care audit event type")
    for value in (item_count, succeeded_count, skipped_count):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > MAX_QUARANTINE_CANDIDATES:
            raise ValueError("audit counters are outside the permitted range")
    if session_id is not None and (not isinstance(session_id, str) or not re.fullmatch(r"session-[0-9]{1,30}", session_id)):
        raise ValueError("audit session identifier is invalid")
    directory = Path(audit_dir).expanduser() if audit_dir is not None else _default_pc_care_audit_dir()
    if not _no_link_components(directory):
        raise ValueError("PC-care audit directory contains a link/reparse point")
    directory.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(directory) or not _no_link_components(directory):
        raise ValueError("PC-care audit directory failed path-safety validation")
    event_id = time.time_ns()
    record = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "event_id": event_id,
        "timestamp_epoch": int(time.time()),
        "operation": operation,
        "phase": phase,
        "status": str(status)[:80],
        "item_count": item_count,
        "succeeded_count": succeeded_count,
        "skipped_count": skipped_count,
        "session_id": session_id,
        "files_deleted": 0,
        "network_used": False,
    }
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["sha256"] = hashlib.sha256(canonical).hexdigest()
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    final_path = directory / f"event-{event_id}-{operation}-{phase}.json"
    temporary_path = directory / f".event-{event_id}.tmp"
    if final_path.exists() or temporary_path.exists():
        raise OSError("audit event filename collision")
    with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_path, final_path)
    return {key: value for key, value in record.items() if key != "sha256"} | {"integrity": "written"}


def read_pc_care_audit(*, audit_dir: str | Path | None = None, limit: int = MAX_AUDIT_RECORDS) -> dict:
    """Read the bounded local operation ledger and validate each event checksum."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_AUDIT_RECORDS:
        raise ValueError(f"limit must be between 1 and {MAX_AUDIT_RECORDS}")
    directory = Path(audit_dir).expanduser() if audit_dir is not None else _default_pc_care_audit_dir()
    base_report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "read_only": True,
        "network_used": False,
        "records_returned": 0,
        "records_examined": 0,
        "integrity_status": "verified",
        "items": [],
        "limitations": [
            "The ledger is local and bounded; it is not remote backup.",
            "SHA-256 checksums detect accidental changes but do not authenticate a maliciously rewritten ledger.",
            "Event records intentionally omit exact file paths and file contents.",
        ],
    }
    if not directory.exists():
        return {**base_report, "status": "not_present"}
    if _is_link_or_reparse(directory) or not directory.is_dir() or not _no_link_components(directory):
        return {**base_report, "status": "unsafe_audit_directory", "integrity_status": "unavailable"}
    entries: list[Path] = []
    access_limited = False
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if len(entries) >= MAX_AUDIT_SCAN_FILES:
                    access_limited = True
                    break
                if not entry.name.startswith("event-") or not entry.name.endswith(".json"):
                    continue
                try:
                    target = Path(entry.path)
                    if entry.is_symlink() or _is_link_or_reparse(target) or not entry.is_file(follow_symlinks=False):
                        access_limited = True
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if info.st_size > MAX_AUDIT_RECORD_BYTES:
                        access_limited = True
                        continue
                    entries.append(target)
                except OSError:
                    access_limited = True
    except OSError:
        return {**base_report, "status": "read_error", "integrity_status": "unavailable"}
    entries.sort(key=lambda item: item.name, reverse=True)
    records = []
    total_bytes = 0
    corrupt_count = 0
    for target in entries[:MAX_AUDIT_SCAN_FILES]:
        try:
            info = target.stat(follow_symlinks=False)
            total_bytes += info.st_size
            if total_bytes > MAX_AUDIT_TOTAL_BYTES:
                access_limited = True
                break
            record = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("audit event is not an object")
            claimed = record.pop("sha256", None)
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            actual = hashlib.sha256(canonical).hexdigest()
            valid = isinstance(claimed, str) and re.fullmatch(r"[0-9a-f]{64}", claimed) and claimed == actual
            if not valid or record.get("product") != "BOTPYTON PC Care" or record.get("schema_version") != 1:
                corrupt_count += 1
                records.append({
                    "event_id": record.get("event_id"),
                    "operation": record.get("operation", "unknown"),
                    "phase": record.get("phase", "unknown"),
                    "status": "integrity_error",
                    "integrity": "failed",
                })
            else:
                record["integrity"] = "verified"
                records.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            corrupt_count += 1
            records.append({"operation": "unknown", "phase": "unknown", "status": "unreadable_record", "integrity": "failed"})
    visible = records[:limit]
    integrity_status = "error" if corrupt_count else ("partial" if access_limited else "verified")
    return {
        **base_report,
        "status": "integrity_error" if corrupt_count else ("partial" if access_limited else "collected"),
        "records_returned": len(visible),
        "records_examined": len(records),
        "integrity_status": integrity_status,
        "items": visible,
    }


def _load_valid_quarantine_manifest(manifest_path: str | Path) -> tuple[Path, Path, dict]:
    """Validate a BOTPYTON session location and bounded manifest syntax."""
    path = Path(manifest_path).expanduser()
    if not path.is_absolute() or path.name != "manifest.json" or _is_link_or_reparse(path):
        raise ValueError("Select a regular manifest.json inside a BOTPYTON quarantine session.")
    session_dir = path.parent
    base = session_dir.parent
    if not session_dir.name.startswith("session-") or base.name.casefold() != "quarantine":
        raise ValueError("Manifest is not in a recognized BOTPYTON Quarantine session.")
    if _is_link_or_reparse(session_dir) or _is_link_or_reparse(base) or not _no_link_components(path):
        raise ValueError("Manifest or quarantine path contains a link/reparse point.")
    if path.stat().st_size > 200_000:
        raise ValueError("Quarantine manifest exceeds 200000 bytes.")
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(manifest, dict) or manifest.get("product") != "BOTPYTON PC Care"
            or manifest.get("schema_version") != QUARANTINE_MANIFEST_SCHEMA
            or manifest.get("session_id") != session_dir.name
            or not isinstance(manifest.get("items"), list)
            or len(manifest["items"]) > MAX_QUARANTINE_CANDIDATES):
        raise ValueError("Quarantine manifest is invalid or exceeds its safety limits.")
    claimed_digest = manifest.get("manifest_sha256")
    if (not isinstance(claimed_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", claimed_digest)
            or claimed_digest != _quarantine_manifest_digest(manifest)):
        raise ValueError("Quarantine manifest integrity checksum failed.")
    return path, session_dir, manifest


def _inspect_quarantine_item(
    item: dict,
    session_dir: Path,
    roots: dict[str, Path],
    *,
    hash_budget_remaining: int,
) -> tuple[dict, int]:
    """Read-only integrity and restore-destination check for one manifest item."""
    original_value = item.get("original_path")
    label = item.get("root_label")
    name = item.get("quarantine_name")
    if item.get("status") == "restored":
        return ({"path": original_value, "status": "already_restored", "ready": False}, 0)
    if (not isinstance(original_value, str) or not isinstance(label, str) or label not in roots
            or not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}):
        return ({"path": original_value, "status": "skipped_unapproved_manifest_path", "ready": False}, 0)
    quarantine_file = session_dir / name
    if _is_link_or_reparse(quarantine_file) or not _no_link_components(quarantine_file, stop_at=session_dir):
        return ({"path": original_value, "status": "skipped_unsafe_quarantine_file", "ready": False}, 0)
    try:
        info = quarantine_file.stat(follow_symlinks=False)
        import stat as stat_module
        if not stat_module.S_ISREG(info.st_mode):
            return ({"path": original_value, "status": "skipped_non_regular_quarantine_file", "ready": False}, 0)
    except OSError:
        return ({"path": original_value, "status": "skipped_quarantine_file_missing", "ready": False}, 0)
    expected_size = item.get("size_bytes")
    expected_hash = item.get("sha256")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or info.st_size != expected_size:
        return ({"path": original_value, "status": "skipped_quarantine_file_changed", "ready": False}, 0)
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return ({"path": original_value, "status": "skipped_invalid_integrity_metadata", "ready": False}, 0)
    if info.st_size > hash_budget_remaining:
        return ({"path": original_value, "status": "skipped_hash_budget", "ready": False}, 0)
    try:
        actual_hash = _sha256_file(quarantine_file, byte_limit=hash_budget_remaining)
    except (OSError, ValueError):
        return ({"path": original_value, "status": "skipped_hash_unavailable_or_changed", "ready": False}, 0)
    bytes_used = info.st_size
    if actual_hash != expected_hash:
        return ({"path": original_value, "status": "skipped_integrity_mismatch", "ready": False}, bytes_used)
    destination = _safe_restore_destination(original_value, roots[label])
    if destination is None:
        return ({"path": original_value, "status": "skipped_destination_exists_or_unsafe", "ready": False}, bytes_used)
    return ({"path": original_value, "status": "ready_to_restore", "ready": True}, bytes_used)


def preview_quarantined_restore(
    manifest_path: str | Path,
    *,
    scan_roots: list[tuple[str, Path]] | None = None,
) -> dict:
    """Dry-run recovery checklist; never creates, replaces, or moves files."""
    path, session_dir, manifest = _load_valid_quarantine_manifest(manifest_path)
    roots = _approved_temporary_roots(scan_roots)
    results = []
    total_hashed = 0
    for item in manifest["items"]:
        if not isinstance(item, dict):
            results.append({"status": "skipped_invalid_manifest_entry", "ready": False})
            continue
        inspected, used = _inspect_quarantine_item(
            item, session_dir, roots, hash_budget_remaining=MAX_QUARANTINE_HASH_BYTES - total_hashed
        )
        total_hashed += used
        results.append(inspected)
    ready_count = sum(1 for item in results if item.get("ready") is True)
    skipped_count = sum(1 for item in results if item.get("ready") is False and item.get("status") != "already_restored")
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "preview_complete" if skipped_count == 0 else "preview_partial",
        "read_only": True,
        "network_used": False,
        "manifest_path": str(path),
        "manifest_status": manifest.get("status", "unknown"),
        "candidate_count": len(results),
        "ready_count": ready_count,
        "files_restored": 0,
        "files_skipped": skipped_count,
        "hash_bytes_examined": total_hashed,
        "items": results,
        "limitations": [
            "Dry-run only: no files are restored or changed.",
            "Files with missing or mismatched SHA-256 metadata, a path collision, a link/reparse component, or an unavailable hash are not eligible for restoration.",
            "At most 512 MiB of file contents are hashed during one checklist.",
        ],
    }



def quarantine_cleanup_candidates(
    preview: dict,
    *,
    confirmation: str,
    scan_roots: list[tuple[str, Path]] | None = None,
    quarantine_root: str | Path | None = None,
    now: float | None = None,
    audit_dir: str | Path | None = None,
) -> dict:
    """Move verified stale files to a same-volume quarantine; never unlink files."""
    if confirmation != QUARANTINE_CONFIRMATION:
        raise ValueError("Cleanup cancelled: the exact confirmation phrase was not supplied.")
    if not isinstance(preview, dict) or preview.get("product") != "BOTPYTON PC Care":
        raise ValueError("Cleanup preview is not a BOTPYTON PC Care report.")
    if preview.get("schema_version") != 1 or preview.get("status") != "preview_complete":
        raise ValueError("Cleanup requires a complete preview; partial previews are rejected.")
    if preview.get("read_only") is not True or preview.get("network_used") is not False:
        raise ValueError("Cleanup preview has an invalid safety state.")
    if preview.get("settings_changed") is not False or preview.get("files_deleted") != 0:
        raise ValueError("Cleanup preview is not an untouched read-only report.")
    age_days = preview.get("minimum_age_days")
    candidates = preview.get("candidates")
    if not isinstance(age_days, int) or isinstance(age_days, bool) or not 1 <= age_days <= 365:
        raise ValueError("Cleanup preview has an invalid minimum age.")
    if not isinstance(candidates, list) or len(candidates) > MAX_QUARANTINE_CANDIDATES:
        raise ValueError(f"Cleanup is limited to {MAX_QUARANTINE_CANDIDATES} candidates per run.")
    if preview.get("candidate_count") != len(candidates):
        raise ValueError("Cleanup preview candidate count does not match its candidate list.")

    roots = _approved_temporary_roots(scan_roots)
    now_value = time.time() if now is None else float(now)
    cutoff = now_value - age_days * 86400
    ready: list[dict] = []
    skipped: list[dict] = []
    seen_paths: set[str] = set()
    total_hash_bytes = 0

    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            skipped.append({"index": index, "status": "skipped_invalid_entry"})
            continue
        label = candidate.get("root_label")
        path_value = candidate.get("path")
        if not isinstance(label, str) or label not in roots or not isinstance(path_value, str):
            skipped.append({"index": index, "status": "skipped_unapproved_location"})
            continue
        normalized_path = os.path.normcase(os.path.abspath(path_value))
        if normalized_path in seen_paths:
            skipped.append({"index": index, "path": path_value, "status": "skipped_duplicate"})
            continue
        seen_paths.add(normalized_path)
        info = _safe_regular_file_info(path_value, roots[label])
        if info is None:
            skipped.append({"index": index, "path": path_value, "status": "skipped_unsafe_or_missing"})
            continue
        try:
            expected_size = candidate["size_bytes"]
            expected_mtime = candidate["modified_epoch"]
            expected_mtime_ns = candidate["modified_ns"]
            if (not isinstance(expected_size, int) or isinstance(expected_size, bool)
                    or not isinstance(expected_mtime, (int, float)) or isinstance(expected_mtime, bool)
                    or not isinstance(expected_mtime_ns, int) or isinstance(expected_mtime_ns, bool)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            skipped.append({"index": index, "path": path_value, "status": "skipped_invalid_metadata"})
            continue
        if info.st_size != expected_size or int(info.st_mtime) != int(expected_mtime) or info.st_mtime_ns != expected_mtime_ns:
            skipped.append({"index": index, "path": path_value, "status": "skipped_changed_since_preview"})
            continue
        if info.st_mtime > cutoff:
            skipped.append({"index": index, "path": path_value, "status": "skipped_not_old_enough"})
            continue
        remaining_hash_budget = MAX_QUARANTINE_HASH_BYTES - total_hash_bytes
        if info.st_size > remaining_hash_budget:
            skipped.append({"index": index, "path": path_value, "status": "skipped_hash_budget"})
            continue
        try:
            digest = _sha256_file(path_value, byte_limit=remaining_hash_budget)
            after_hash = _safe_regular_file_info(path_value, roots[label])
        except (OSError, ValueError):
            digest = None
            after_hash = None
        if (digest is None or after_hash is None or after_hash.st_size != expected_size
                or after_hash.st_mtime_ns != expected_mtime_ns):
            skipped.append({"index": index, "path": path_value, "status": "skipped_hash_unavailable_or_changed"})
            continue
        total_hash_bytes += after_hash.st_size
        ready.append({
            "original_path": str(Path(path_value)),
            "root_label": label,
            "size_bytes": int(after_hash.st_size),
            "modified_epoch": int(after_hash.st_mtime),
            "modified_ns": int(after_hash.st_mtime_ns),
            "sha256": digest,
        })

    report = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "no_candidates" if not ready else "not_started",
        "read_only": False,
        "network_used": False,
        "files_deleted": 0,
        "files_quarantined": 0,
        "files_skipped": len(skipped),
        "settings_changed": False,
        "items": skipped,
        "manifest_path": None,
        "limitations": [
            "Files are moved, not deleted. Keep the manifest to restore them.",
            "Moves across different volumes may be rejected by the operating system; rejected files stay at their original paths.",
            "Files in use, changed, linked, protected, inaccessible, or outside approved temporary roots are skipped.",
            "Quarantine does not prove a file is unnecessary; review the manifest and restore any needed file.",
        ],
    }
    if not ready:
        return report

    base = Path(quarantine_root).expanduser() if quarantine_root is not None else _default_quarantine_root()
    if _path_is_inside_any_root(base, roots):
        raise ValueError("Quarantine directory must be outside all approved temporary roots.")
    if not _no_link_components(base):
        raise ValueError("Quarantine directory or one of its existing parents is a link/reparse point.")
    base.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(base) or _path_is_inside_any_root(base, roots):
        raise ValueError("Quarantine directory failed path-safety validation.")

    session_name = f"session-{time.time_ns()}"
    session_dir = base / session_name
    session_dir.mkdir(parents=False, exist_ok=False)
    if _is_link_or_reparse(session_dir):
        raise ValueError("Quarantine session directory failed path-safety validation.")
    manifest_path = session_dir / "manifest.json"
    manifest_items = []
    for index, item in enumerate(ready, start=1):
        basename = Path(item["original_path"]).name[:160] or "unnamed"
        manifest_items.append({
            **item,
            "quarantine_name": f"{index:04d}-{basename}",
            "status": "pending",
        })
    manifest = {
        "product": "BOTPYTON PC Care",
        "schema_version": QUARANTINE_MANIFEST_SCHEMA,
        "session_id": session_name,
        "created_epoch": int(now_value),
        "status": "in_progress",
        "items": manifest_items,
    }
    _write_quarantine_manifest(manifest_path, manifest)
    report["manifest_path"] = str(manifest_path)
    report["status"] = "in_progress"
    report["audit_status"] = "unavailable"
    try:
        _persist_pc_care_audit_event(
            "quarantine", "started", "in_progress",
            item_count=len(manifest_items), session_id=session_name, audit_dir=audit_dir,
        )
    except (OSError, ValueError):
        report["status"] = "audit_unavailable"
        report["files_quarantined"] = 0
        report["audit_status"] = "write_failed"
        return report
    report["audit_status"] = "written"

    for item in manifest_items:
        source = Path(item["original_path"])
        source_root = roots[item["root_label"]]
        fresh_info = _safe_regular_file_info(source, source_root)
        if (fresh_info is None or fresh_info.st_size != item["size_bytes"]
                or int(fresh_info.st_mtime) != item["modified_epoch"]
                or fresh_info.st_mtime_ns != item["modified_ns"]
                or fresh_info.st_mtime > cutoff):
            item["status"] = "skipped_changed_before_move"
            report["items"].append({"path": item["original_path"], "status": item["status"]})
            _write_quarantine_manifest(manifest_path, manifest)
            continue
        destination = session_dir / item["quarantine_name"]
        if destination.exists() or destination.is_symlink() or not _no_link_components(session_dir):
            item["status"] = "skipped_quarantine_path_unsafe"
            report["items"].append({"path": item["original_path"], "status": item["status"]})
            _write_quarantine_manifest(manifest_path, manifest)
            continue
        try:
            _move_no_replace(source, destination)
        except OSError as exc:
            item["status"] = "move_failed"
            item["failure_type"] = type(exc).__name__
            report["items"].append({
                "path": item["original_path"],
                "status": item["status"],
                "failure_type": type(exc).__name__,
            })
            _write_quarantine_manifest(manifest_path, manifest)
            continue
        item["status"] = "quarantined"
        try:
            _write_quarantine_manifest(manifest_path, manifest)
        except OSError:
            report["items"].append({
                "path": item["original_path"],
                "status": "moved_manifest_update_failed",
            })
            report["status"] = "manifest_write_failed"
            break
        report["items"].append({
            "path": item["original_path"],
            "quarantine_path": str(destination),
            "status": "quarantined",
            "size_bytes": item["size_bytes"],
        })

    report["files_quarantined"] = sum(
        1 for item in manifest_items
        if item["status"] == "quarantined" or (
            item["status"] == "pending"
            and (session_dir / item["quarantine_name"]).is_file()
            and not Path(item["original_path"]).exists()
        )
    )
    report["files_skipped"] = sum(1 for item in report["items"] if str(item.get("status", "")).startswith("skipped") or item.get("status") == "move_failed")
    if report["status"] != "manifest_write_failed":
        report["status"] = "completed" if report["files_skipped"] == 0 else "partial"
    try:
        _persist_pc_care_audit_event(
            "quarantine", "completed", report["status"],
            item_count=len(manifest_items), succeeded_count=report["files_quarantined"],
            skipped_count=report["files_skipped"], session_id=session_name, audit_dir=audit_dir,
        )
    except (OSError, ValueError):
        report["audit_status"] = "final_write_failed"
        if report["status"] == "completed":
            report["status"] = "audit_write_failed"
    return report


def restore_quarantined_files(
    manifest_path: str | Path,
    *,
    confirmation: str,
    scan_roots: list[tuple[str, Path]] | None = None,
    audit_dir: str | Path | None = None,
) -> dict:
    """Restore verified quarantined files to their original approved temp locations; never overwrite."""
    if confirmation != RESTORE_CONFIRMATION:
        raise ValueError("Restore cancelled: the exact confirmation phrase was not supplied.")
    path, session_dir, manifest = _load_valid_quarantine_manifest(manifest_path)
    roots = _approved_temporary_roots(scan_roots)
    try:
        _persist_pc_care_audit_event(
            "restore", "started", "in_progress",
            item_count=len(manifest["items"]), session_id=session_dir.name, audit_dir=audit_dir,
        )
    except (OSError, ValueError):
        return {
            "product": "BOTPYTON PC Care", "schema_version": 1,
            "status": "audit_unavailable", "read_only": False, "network_used": False,
            "files_restored": 0, "files_skipped": len(manifest["items"]),
            "files_deleted": 0, "settings_changed": False, "manifest_path": str(path),
            "items": [], "audit_status": "write_failed",
            "limitations": ["Restore was not started because its audit start record could not be persisted."],
        }

    restored: list[dict] = []
    skipped: list[dict] = []
    total_hashed = 0
    for item in manifest["items"]:
        if not isinstance(item, dict):
            skipped.append({"status": "skipped_invalid_manifest_entry"})
            continue
        inspected, used = _inspect_quarantine_item(
            item, session_dir, roots,
            hash_budget_remaining=MAX_QUARANTINE_HASH_BYTES - total_hashed,
        )
        total_hashed += used
        if inspected["status"] == "already_restored":
            skipped.append({"path": inspected.get("path"), "status": "already_restored"})
            continue
        if not inspected.get("ready"):
            skipped.append({"path": inspected.get("path"), "status": inspected["status"]})
            continue
        original_value = item["original_path"]
        name = item["quarantine_name"]
        quarantine_file = session_dir / name
        destination = _safe_restore_destination(original_value, roots[item["root_label"]])
        if destination is None:
            skipped.append({"path": original_value, "status": "skipped_destination_exists_or_unsafe"})
            continue
        # The SHA-256 check above is already complete. Recheck metadata and destination
        # immediately before the atomic no-overwrite move without hashing the bytes twice.
        try:
            final_info = quarantine_file.stat(follow_symlinks=False)
            expected_mtime_ns = item.get("modified_ns")
            if (not quarantine_file.is_file() or final_info.st_size != item["size_bytes"]
                    or not isinstance(expected_mtime_ns, int)
                    or final_info.st_mtime_ns != expected_mtime_ns):
                skipped.append({"path": original_value, "status": "skipped_quarantine_file_changed"})
                continue
        except OSError:
            skipped.append({"path": original_value, "status": "skipped_quarantine_file_missing"})
            continue
        destination = _safe_restore_destination(original_value, roots[item["root_label"]])
        if destination is None:
            skipped.append({"path": original_value, "status": "skipped_destination_exists_or_unsafe"})
            continue
        item["status"] = "restoring"
        _write_quarantine_manifest(path, manifest)
        try:
            _move_no_replace(quarantine_file, destination)
        except OSError as exc:
            item["status"] = "quarantined"
            item["failure_type"] = type(exc).__name__
            _write_quarantine_manifest(path, manifest)
            skipped.append({"path": original_value, "status": "restore_failed", "failure_type": type(exc).__name__})
            continue
        item["status"] = "restored"
        _write_quarantine_manifest(path, manifest)
        restored.append({"path": str(destination), "status": "restored"})

    manifest["status"] = "restored" if not any(
        isinstance(item, dict) and item.get("status") in {"quarantined", "pending", "restoring"}
        for item in manifest["items"]
    ) else "partial"
    _write_quarantine_manifest(path, manifest)
    final_status = "restored" if not skipped else ("partial" if restored else "no_files_restored")
    audit_status = "written"
    try:
        _persist_pc_care_audit_event(
            "restore", "completed", final_status,
            item_count=len(manifest["items"]), succeeded_count=len(restored),
            skipped_count=len(skipped), session_id=session_dir.name, audit_dir=audit_dir,
        )
    except (OSError, ValueError):
        audit_status = "final_write_failed"
        if final_status == "restored":
            final_status = "audit_write_failed"
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": final_status,
        "read_only": False,
        "network_used": False,
        "files_restored": len(restored),
        "files_skipped": len(skipped),
        "manifest_path": str(path),
        "hash_bytes_examined": total_hashed,
        "audit_status": audit_status,
        "items": restored + skipped,
        "files_deleted": 0,
        "settings_changed": False,
        "limitations": [
            "SHA-256 integrity must match the quarantine manifest before a file is restored.",
            "Existing original paths are never overwritten.",
            "Restoration is restricted to approved temporary roots recorded in the manifest.",
            "Cross-volume moves may be rejected; a failed restore leaves the file in quarantine.",
            "At most 512 MiB of file contents are hashed per run.",
        ],
    }


def get_pc_care_report(
    *,
    scan_roots: list[tuple[str, Path]] | None = None,
    startup_items: dict | None = None,
    process_items: dict | None = None,
    application_items: dict | None = None,
    windows: bool | None = None,
    now: float | None = None,
) -> dict:
    """Create a local report. Does not write files, access the network, or alter Windows."""
    is_windows = os.name == "nt" if windows is None else windows
    profile = get_system_profile(Path.home())
    roots = default_scan_roots() if scan_roots is None else scan_roots
    scans = []
    total_candidate_bytes = 0
    total_candidate_files = 0
    total_access_errors = 0
    partial_scan = False

    for label, path in roots[:32]:
        scan = scan_stale_files(path, now=now)
        scan["label"] = label
        scans.append(scan)
        total_candidate_bytes += scan["candidate_bytes"]
        total_candidate_files += scan["candidate_files"]
        total_access_errors += scan["access_errors"]
        partial_scan = partial_scan or scan["status"] == "scan_partial"

    try:
        disk = shutil.disk_usage(Path.home())
        disk_report = {
            "total_bytes": int(disk.total),
            "free_bytes": int(disk.free),
            "free_gib": round(disk.free / (1024 ** 3), 2),
            "free_percent": round((disk.free / disk.total) * 100, 2) if disk.total else None,
        }
    except OSError:
        disk_report = {"status": "unavailable"}

    startup = read_startup_entries(windows=is_windows) if startup_items is None else startup_items
    processes = read_top_processes(windows=is_windows) if process_items is None else process_items
    applications = read_installed_applications(windows=is_windows) if application_items is None else application_items
    findings: list[dict] = []

    free_percent = disk_report.get("free_percent")
    free_bytes = disk_report.get("free_bytes")
    if free_percent is not None and free_bytes is not None and (free_percent < 10 or free_bytes < 10 * (1024 ** 3)):
        findings.append({
            "id": "disk_space_low",
            "severity": "warning",
            "title": "Low free disk space",
            "detail": f"Only {disk_report['free_gib']} GiB free ({free_percent}%).",
            "recommendation": "Review large personal files and the cleanup estimate before removing anything.",
        })

    available_mb = profile.get("available_memory_mb")
    if isinstance(available_mb, int) and available_mb < 1024:
        findings.append({
            "id": "available_memory_low",
            "severity": "warning",
            "title": "Low available physical memory",
            "detail": f"Approximately {available_mb} MiB of physical memory is available at scan time.",
            "recommendation": "Inspect current heavy processes and browser tabs; this measurement changes over time.",
        })
    elif isinstance(available_mb, int) and available_mb < 2048:
        findings.append({
            "id": "available_memory_limited",
            "severity": "info",
            "title": "Available memory is limited",
            "detail": f"Approximately {available_mb} MiB of physical memory is available at scan time.",
            "recommendation": "Close unused applications before launching memory-heavy tasks.",
        })

    if total_candidate_files:
        findings.append({
            "id": "old_temp_or_cache_files",
            "severity": "info",
            "title": "Older temporary/cache files found",
            "detail": f"{total_candidate_files} files older than {STALE_DAYS} days were identified by metadata.",
            "estimated_bytes": total_candidate_bytes,
            "recommendation": "Treat this as an estimate only. Files may be in use and are not deleted by this audit.",
        })

    if processes.get("status") == "collected" and processes.get("items"):
        top = processes["items"][0]
        findings.append({
            "id": "process_memory_snapshot",
            "severity": "info",
            "title": "Processes using the most memory",
            "detail": f"{top['name']} (PID {top['pid']}) is currently using approximately {round(top['memory_kib'] / 1024)} MiB of working-set memory.",
            "recommendation": "Use this as a snapshot, not a verdict. Save work before closing an application, and do not terminate unfamiliar system processes.",
        })

    if startup.get("items_count", 0):
        findings.append({
            "id": "startup_entries_present",
            "severity": "info",
            "title": "Programs registered to start automatically",
            "detail": f"{startup['items_count']} startup entries were found.",
            "recommendation": "An entry is not proof of slowdown. Review the name and publisher before disabling anything.",
        })

    if not is_windows:
        platform_status = "unsupported_platform"
        driver_status = "not_checked_non_windows"
        app_update_status = "not_checked_non_windows"
    else:
        platform_status = "windows_local_inventory"
        driver_status = "not_checked"
        app_update_status = "not_checked"

    if is_windows:
        findings.append({
            "id": "driver_versions_not_verified",
            "severity": "unknown",
            "title": "Driver freshness not verified",
            "detail": "A local version/date alone cannot establish that a driver is obsolete or compatible.",
            "recommendation": "Use Windows Update or the PC/component manufacturer's official support page; do not install a generic driver pack.",
        })
        if applications.get("status") == "collected":
            findings.append({
                "id": "installed_application_inventory",
                "severity": "info",
                "title": "Installed application inventory collected",
                "detail": f"{applications['items_count']} installed application entries were found.",
                "recommendation": "Inventory is not an update check. Compare versions only against the app publisher or a trusted package source.",
            })
        findings.append({
            "id": "application_updates_not_checked",
            "severity": "unknown",
            "title": "Application updates not checked",
            "detail": "This first audit is offline and does not query software repositories.",
            "recommendation": "Check updates in the application's own updater or Microsoft Store. An online check can be added as a separate, opt-in phase.",
        })

    status = "partial" if partial_scan or total_access_errors else ("diagnostic_only" if is_windows else "unsupported_platform")
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": status,
        "platform_status": platform_status,
        "read_only": True,
        "network_used": False,
        "ai_enabled": False,
        "files_deleted": 0,
        "settings_changed": False,
        "drivers_updated": False,
        "applications_updated": False,
        "system": {
            "os": profile.get("os"),
            "cpu_count": profile.get("cpu_count"),
            "available_memory_mb": profile.get("available_memory_mb"),
            "disk": disk_report,
        },
        "cleanup_estimate": {
            "older_than_days": STALE_DAYS,
            "candidate_files": total_candidate_files,
            "candidate_bytes": total_candidate_bytes,
            "candidate_mib": round(total_candidate_bytes / (1024 ** 2), 2),
            "guaranteed_reclaimable": False,
            "scan_limited": partial_scan,
            "access_errors": total_access_errors,
        },
        "scan_locations": scans,
        "startup_inventory": startup,
        "process_inventory": processes,
        "application_inventory": applications,
        "driver_updates": {"status": driver_status},
        "application_updates": {"status": app_update_status},
        "findings": findings,
        "limitations": [
            "The audit is read-only: it does not delete files, stop processes, change startup settings, or edit the registry.",
            "Only selected per-user temp/cache folders are scanned, with a bounded entry/depth limit; this is not a full disk search.",
            "A large number of installed applications does not by itself prove that a PC is slow.",
            "Old driver dates are not enough to label a driver obsolete; compatibility and vendor/Windows Update data matter.",
            "The report may contain local folder paths. Review it before sharing publicly.",
        ],
    }
