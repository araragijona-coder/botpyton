"""Bounded, read-only preview of explicitly allowlisted desktop-app caches."""
from __future__ import annotations

import os
import time
from pathlib import Path

MAX_ENTRIES = 2_000
MAX_CANDIDATES = 150
MAX_SECONDS = 8.0
MAX_DEPTH = 3
REPARSE_POINT_ATTRIBUTE = 0x400

# These Electron cache areas are intentionally separate from browser profiles.
# Never add app data, databases, sessions, local storage, or downloaded media here.
APP_PROFILES = (
    ("Discord", "APPDATA", "discord"),
    ("Slack", "APPDATA", "Slack"),
)
CACHE_AREAS = ("Cache", "Code Cache", "GPUCache")


def _unsafe(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)
    except OSError:
        return True


def preview_desktop_app_cache(*, env: dict[str, str] | None = None,
                              windows: bool | None = None, now: float | None = None,
                              minimum_age_days: int = 1, max_entries: int = 1_000,
                              max_candidates: int = 100, timeout_seconds: float = 5.0) -> dict:
    """List old files in fixed Discord/Slack Electron cache folders; never mutates data."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(minimum_age_days, int) or isinstance(minimum_age_days, bool) or not 1 <= minimum_age_days <= 365:
        raise ValueError("minimum_age_days must be between 1 and 365")
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= MAX_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_ENTRIES}")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES}")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= MAX_SECONDS:
        raise ValueError(f"timeout_seconds must be between 0.1 and {MAX_SECONDS}")
    report = {
        "product": "BOTPYTON PC Care", "schema_version": 1,
        "operation": "desktop_app_cache_preview", "read_only": True,
        "network_used": False, "ai_enabled": False, "files_deleted": 0,
        "files_moved": 0, "settings_changed": False,
        "minimum_age_days": minimum_age_days, "entry_limit": max_entries,
        "candidate_limit": max_candidates, "timeout_seconds": float(timeout_seconds),
    }
    if not is_windows:
        return {**report, "status": "unsupported_platform", "entries_examined": 0,
                "cache_roots_examined": 0, "access_errors": 0, "scan_limited": False,
                "candidate_count": 0, "candidate_bytes": 0, "candidates": [],
                "note": "Desktop application cache preview currently supports Windows only."}
    environ = os.environ if env is None else env
    now_value = time.time() if now is None else float(now)
    cutoff = now_value - minimum_age_days * 86400
    deadline = time.monotonic() + float(timeout_seconds)
    candidates: list[dict] = []
    entries = roots = errors = unsafe_count = 0
    limited = False
    stop = False
    for app, env_key, dirname in APP_PROFILES:
        base = environ.get(env_key)
        if not base:
            continue
        app_root = Path(base).expanduser() / dirname
        try:
            if not app_root.exists():
                continue
            if not app_root.is_dir() or _unsafe(app_root):
                unsafe_count += 1
                limited = True
                continue
            for area in CACHE_AREAS:
                cache_root = app_root / area
                try:
                    if not cache_root.exists():
                        continue
                    if not cache_root.is_dir() or _unsafe(cache_root):
                        unsafe_count += 1
                        limited = True
                        continue
                    resolved_root = cache_root.resolve(strict=True)
                    roots += 1
                    stack: list[tuple[Path, int]] = [(resolved_root, 0)]
                    while stack and not stop:
                        if time.monotonic() >= deadline or entries >= max_entries:
                            limited = stop = True
                            break
                        current, depth = stack.pop()
                        with os.scandir(current) as iterator:
                            for item in iterator:
                                if time.monotonic() >= deadline or entries >= max_entries:
                                    limited = stop = True
                                    break
                                entries += 1
                                item_path = Path(item.path)
                                try:
                                    if item.is_symlink() or _unsafe(item_path):
                                        unsafe_count += 1
                                        limited = True
                                        continue
                                    if item.is_dir(follow_symlinks=False):
                                        if depth < MAX_DEPTH:
                                            stack.append((item_path, depth + 1))
                                        else:
                                            limited = True
                                        continue
                                    if not item.is_file(follow_symlinks=False):
                                        continue
                                    info = item.stat(follow_symlinks=False)
                                    if info.st_mtime > cutoff:
                                        continue
                                    resolved = item_path.resolve(strict=True)
                                    if not resolved.is_relative_to(resolved_root):
                                        unsafe_count += 1
                                        limited = True
                                        continue
                                    candidates.append({
                                        "application": app, "cache_area": area,
                                        "cache_root": str(resolved_root), "path": str(resolved),
                                        "size_bytes": max(0, int(info.st_size)),
                                        "modified_ns": int(info.st_mtime_ns),
                                        "age_days": max(0, int((now_value - info.st_mtime) // 86400)),
                                        "status": "preview_only",
                                    })
                                    if len(candidates) >= max_candidates:
                                        limited = stop = True
                                        break
                                except OSError:
                                    errors += 1
                except OSError:
                    errors += 1
                if stop:
                    break
        except OSError:
            errors += 1
        if stop:
            break
    total_bytes = sum(item["size_bytes"] for item in candidates)
    partial = limited or errors > 0
    return {
        **report, "status": "preview_partial" if partial else "preview_complete",
        "entries_examined": entries, "cache_roots_examined": roots,
        "access_errors": errors, "unsafe_paths_skipped": unsafe_count,
        "scan_limited": limited, "candidate_count": len(candidates),
        "candidate_bytes": total_bytes, "candidate_mib": round(total_bytes / (1024 ** 2), 2),
        "candidates": candidates,
        "limitations": [
            "Preview only: no contents are read, moved, or deleted; no settings are changed.",
            "Only Discord and Slack Cache, Code Cache, and GPUCache folders are eligible.",
            "Application databases, Local Storage, IndexedDB, cookies, tokens, sessions, settings, attachments and downloaded/media content are excluded.",
            "This is not connected to the browser-cache quarantine workflow; no new app profile can be quarantined in this phase.",
            "Close the application before any future separately reviewed cleanup. Old age alone does not prove a file is unused.",
            "Partial results are not exhaustive; report paths may reveal local usernames.",
        ],
        "note": "This is a read-only inventory of recognized Electron cache folders, not authorization to remove files.",
    }
