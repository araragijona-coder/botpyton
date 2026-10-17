"""Read-only, offline PC-care inventory; no cleanup or settings changes."""
from __future__ import annotations

import csv
import io
import os
import shutil
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


def get_pc_care_report(
    *,
    scan_roots: list[tuple[str, Path]] | None = None,
    startup_items: dict | None = None,
    process_items: dict | None = None,
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
