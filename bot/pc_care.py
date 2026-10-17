"""Local PC-care inventory with explicitly confirmed, reversible temp-file quarantine."""
from __future__ import annotations

import csv
import io
import json
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



QUARANTINE_CONFIRMATION = "QUARANTINE OLD TEMP FILES"
RESTORE_CONFIRMATION = "RESTORE BOTPYTON QUARANTINE"
MAX_QUARANTINE_CANDIDATES = 100
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


def _write_quarantine_manifest(manifest_path: Path, manifest: dict) -> None:
    """Atomically persist the recovery information before/after each move."""
    temporary_path = manifest_path.with_name(".manifest.json.tmp")
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_path.write_text(payload, encoding="utf-8")
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


def quarantine_cleanup_candidates(
    preview: dict,
    *,
    confirmation: str,
    scan_roots: list[tuple[str, Path]] | None = None,
    quarantine_root: str | Path | None = None,
    now: float | None = None,
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
            if (not isinstance(expected_size, int) or isinstance(expected_size, bool)
                    or not isinstance(expected_mtime, (int, float)) or isinstance(expected_mtime, bool)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            skipped.append({"index": index, "path": path_value, "status": "skipped_invalid_metadata"})
            continue
        if info.st_size != expected_size or int(info.st_mtime) != int(expected_mtime):
            skipped.append({"index": index, "path": path_value, "status": "skipped_changed_since_preview"})
            continue
        if info.st_mtime > cutoff:
            skipped.append({"index": index, "path": path_value, "status": "skipped_not_old_enough"})
            continue
        ready.append({
            "original_path": str(Path(path_value)),
            "root_label": label,
            "size_bytes": int(info.st_size),
            "modified_epoch": int(info.st_mtime),
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

    for item in manifest_items:
        source = Path(item["original_path"])
        source_root = roots[item["root_label"]]
        fresh_info = _safe_regular_file_info(source, source_root)
        if (fresh_info is None or fresh_info.st_size != item["size_bytes"]
                or int(fresh_info.st_mtime) != item["modified_epoch"]
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
            os.replace(source, destination)
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
    return report


def restore_quarantined_files(
    manifest_path: str | Path,
    *,
    confirmation: str,
    scan_roots: list[tuple[str, Path]] | None = None,
) -> dict:
    """Restore quarantined files to their original approved temp locations; never overwrite."""
    if confirmation != RESTORE_CONFIRMATION:
        raise ValueError("Restore cancelled: the exact confirmation phrase was not supplied.")
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

    roots = _approved_temporary_roots(scan_roots)
    restored: list[dict] = []
    skipped: list[dict] = []
    for item in manifest["items"]:
        if not isinstance(item, dict):
            skipped.append({"status": "skipped_invalid_manifest_entry"})
            continue
        if item.get("status") == "restored":
            skipped.append({"path": item.get("original_path"), "status": "already_restored"})
            continue
        original_value = item.get("original_path")
        label = item.get("root_label")
        name = item.get("quarantine_name")
        if (not isinstance(original_value, str) or not isinstance(label, str)
                or label not in roots or not isinstance(name, str)
                or Path(name).name != name or name in {"", ".", ".."}):
            skipped.append({"path": original_value, "status": "skipped_unapproved_manifest_path"})
            continue
        quarantine_file = session_dir / name
        if _is_link_or_reparse(quarantine_file) or not _no_link_components(quarantine_file, stop_at=session_dir):
            skipped.append({"path": original_value, "status": "skipped_unsafe_quarantine_file"})
            continue
        try:
            quarantine_info = quarantine_file.stat(follow_symlinks=False)
            import stat as stat_module
            if not stat_module.S_ISREG(quarantine_info.st_mode):
                skipped.append({"path": original_value, "status": "skipped_non_regular_quarantine_file"})
                continue
            expected_size = item.get("size_bytes")
            if not isinstance(expected_size, int) or isinstance(expected_size, bool) or quarantine_info.st_size != expected_size:
                skipped.append({"path": original_value, "status": "skipped_quarantine_file_changed"})
                continue
        except OSError:
            skipped.append({"path": original_value, "status": "skipped_quarantine_file_missing"})
            continue
        destination = _safe_restore_destination(original_value, roots[label])
        if destination is None:
            skipped.append({"path": original_value, "status": "skipped_destination_exists_or_unsafe"})
            continue
        item["status"] = "restoring"
        _write_quarantine_manifest(path, manifest)
        try:
            os.replace(quarantine_file, destination)
        except OSError as exc:
            item["status"] = "quarantined"
            item["failure_type"] = type(exc).__name__
            _write_quarantine_manifest(path, manifest)
            skipped.append({
                "path": original_value,
                "status": "restore_failed",
                "failure_type": type(exc).__name__,
            })
            continue
        item["status"] = "restored"
        _write_quarantine_manifest(path, manifest)
        restored.append({"path": str(destination), "status": "restored"})

    manifest["status"] = "restored" if not any(
        isinstance(item, dict) and item.get("status") in {"quarantined", "pending", "restoring"}
        for item in manifest["items"]
    ) else "partial"
    _write_quarantine_manifest(path, manifest)
    return {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "status": "restored" if not skipped else ("partial" if restored else "no_files_restored"),
        "read_only": False,
        "network_used": False,
        "files_restored": len(restored),
        "files_skipped": len(skipped),
        "manifest_path": str(path),
        "items": restored + skipped,
        "files_deleted": 0,
        "settings_changed": False,
        "limitations": [
            "Existing original paths are never overwritten.",
            "Restoration is restricted to approved temporary roots recorded in the manifest.",
            "Cross-volume moves may be rejected; a failed restore leaves the file in quarantine.",
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
