"""Read-only, allowlisted application-cache preview for Windows."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

MAX_APPLICATION_CACHE_ENTRIES = 5_000
MAX_APPLICATION_CACHE_CANDIDATES = 300
MAX_APPLICATION_CACHE_SECONDS = 10.0
MAX_PROFILE_DIRECTORY_ENTRIES = 100
MAX_APPLICATION_PROFILES = 50
MAX_CACHE_DEPTH = 4
REPARSE_POINT_ATTRIBUTE = 0x400

_CHROMIUM_PROFILES = re.compile(r"^(?:Default|Profile [0-9]{1,3})$")
_FIREFOX_PROFILES = re.compile(r"^[A-Za-z0-9._-]{1,100}\.(?:default|default-release|default-esr)$")
_CHROMIUM_CACHE_AREAS = ("Cache", "Code Cache", "GPUCache")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)
    except OSError:
        return True


def _known_profile_roots(env: dict[str, str]) -> list[tuple[str, Path, str]]:
    local = env.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local).expanduser()
    return [
        ("Google Chrome", root / "Google" / "Chrome" / "User Data", "chromium"),
        ("Microsoft Edge", root / "Microsoft" / "Edge" / "User Data", "chromium"),
        ("Brave", root / "BraveSoftware" / "Brave-Browser" / "User Data", "chromium"),
        ("Mozilla Firefox", root / "Mozilla" / "Firefox" / "Profiles", "firefox"),
    ]


def _profile_matches(name: str, kind: str) -> bool:
    if kind == "chromium":
        return bool(_CHROMIUM_PROFILES.fullmatch(name))
    return bool(_FIREFOX_PROFILES.fullmatch(name))


def _safe_directory(path: Path, profile: Path) -> bool:
    """Accept only an existing real directory below its recognized browser profile."""
    try:
        if not path.is_dir() or _is_link_or_reparse(path):
            return False
        resolved_profile = profile.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_profile):
            return False
        relative = path.relative_to(profile)
        current = profile
        if _is_link_or_reparse(current):
            return False
        for part in relative.parts:
            current = current / part
            if _is_link_or_reparse(current):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _cache_roots_for_profile(profile: Path, kind: str) -> list[tuple[str, Path]]:
    if kind == "firefox":
        candidate = profile / "cache2"
        return [("HTTP cache", candidate)] if candidate.exists() else []
    found: list[tuple[str, Path]] = []
    for area in _CHROMIUM_CACHE_AREAS:
        candidate = profile / area
        if area == "Cache":
            nested = candidate / "Cache_Data"
            if nested.is_dir() and not _is_link_or_reparse(nested):
                candidate = nested
        if candidate.exists():
            found.append((area, candidate))
    return found


def preview_application_cache(
    *,
    env: dict[str, str] | None = None,
    windows: bool | None = None,
    now: float | None = None,
    minimum_age_days: int = 1,
    max_entries: int = 3_000,
    max_candidates: int = 200,
    timeout_seconds: float = 5.0,
) -> dict:
    """List old files in recognized browser cache directories; never changes data."""
    is_windows = os.name == "nt" if windows is None else windows
    if not isinstance(minimum_age_days, int) or isinstance(minimum_age_days, bool) or not 1 <= minimum_age_days <= 365:
        raise ValueError("minimum_age_days must be between 1 and 365")
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= MAX_APPLICATION_CACHE_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_APPLICATION_CACHE_ENTRIES}")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= MAX_APPLICATION_CACHE_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_APPLICATION_CACHE_CANDIDATES}")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= MAX_APPLICATION_CACHE_SECONDS:
        raise ValueError(f"timeout_seconds must be between 0.1 and {MAX_APPLICATION_CACHE_SECONDS}")

    base = {
        "product": "BOTPYTON PC Care",
        "schema_version": 1,
        "operation": "application_cache_preview",
        "read_only": True,
        "network_used": False,
        "ai_enabled": False,
        "files_deleted": 0,
        "files_moved": 0,
        "settings_changed": False,
        "minimum_age_days": minimum_age_days,
        "entry_limit": max_entries,
        "candidate_limit": max_candidates,
        "timeout_seconds": float(timeout_seconds),
    }
    if not is_windows:
        return {
            **base, "status": "unsupported_platform", "profiles_examined": 0,
            "cache_roots_examined": 0, "entries_examined": 0, "access_errors": 0,
            "scan_limited": False, "candidate_count": 0, "candidate_bytes": 0,
            "candidates": [], "note": "Application-cache profiles are currently supported only on Windows.",
        }

    environ = os.environ if env is None else env
    profile_roots = _known_profile_roots(environ)
    now_value = time.time() if now is None else float(now)
    cutoff = now_value - minimum_age_days * 86400
    deadline = time.monotonic() + float(timeout_seconds)
    candidates: list[dict] = []
    entries_examined = 0
    profile_entries_examined = 0
    profiles_examined = 0
    cache_roots_examined = 0
    access_errors = 0
    scan_limited = False
    unsafe_paths_skipped = 0
    stop = False

    for browser, profile_root, kind in profile_roots:
        if stop:
            break
        try:
            if not profile_root.exists():
                continue
            if not profile_root.is_dir() or _is_link_or_reparse(profile_root):
                unsafe_paths_skipped += 1
                scan_limited = True
                continue
            with os.scandir(profile_root) as profile_entries:
                for entry in profile_entries:
                    if time.monotonic() >= deadline or profile_entries_examined >= MAX_PROFILE_DIRECTORY_ENTRIES:
                        scan_limited = True
                        stop = True
                        break
                    profile_entries_examined += 1
                    try:
                        if not _profile_matches(entry.name, kind):
                            continue
                        profile = Path(entry.path)
                        if entry.is_symlink() or _is_link_or_reparse(profile) or not entry.is_dir(follow_symlinks=False):
                            unsafe_paths_skipped += 1
                            scan_limited = True
                            continue
                        profiles_examined += 1
                        if profiles_examined > MAX_APPLICATION_PROFILES:
                            scan_limited = True
                            stop = True
                            break
                        for area, cache_root in _cache_roots_for_profile(profile, kind):
                            if stop:
                                break
                            if time.monotonic() >= deadline:
                                scan_limited = True
                                stop = True
                                break
                            if not _safe_directory(cache_root, profile):
                                if cache_root.exists():
                                    unsafe_paths_skipped += 1
                                    scan_limited = True
                                continue
                            cache_roots_examined += 1
                            resolved_root = cache_root.resolve(strict=True)
                            stack: list[tuple[Path, int]] = [(resolved_root, 0)]
                            while stack:
                                if time.monotonic() >= deadline:
                                    scan_limited = True
                                    stop = True
                                    break
                                current, depth = stack.pop()
                                try:
                                    with os.scandir(current) as entries:
                                        for item in entries:
                                            if time.monotonic() >= deadline or entries_examined >= max_entries:
                                                scan_limited = True
                                                stop = True
                                                break
                                            entries_examined += 1
                                            try:
                                                item_path = Path(item.path)
                                                if item.is_symlink() or _is_link_or_reparse(item_path):
                                                    unsafe_paths_skipped += 1
                                                    scan_limited = True
                                                    continue
                                                if item.is_dir(follow_symlinks=False):
                                                    if depth < MAX_CACHE_DEPTH:
                                                        stack.append((item_path, depth + 1))
                                                    else:
                                                        scan_limited = True
                                                    continue
                                                if not item.is_file(follow_symlinks=False):
                                                    continue
                                                info = item.stat(follow_symlinks=False)
                                                if info.st_mtime > cutoff:
                                                    continue
                                                resolved_file = item_path.resolve(strict=True)
                                                if not resolved_file.is_relative_to(resolved_root):
                                                    unsafe_paths_skipped += 1
                                                    scan_limited = True
                                                    continue
                                                candidates.append({
                                                    "browser": browser, "profile": profile.name, "cache_area": area,
                                                    "cache_root": str(resolved_root),
                                                    "path": str(resolved_file), "size_bytes": max(0, int(info.st_size)),
                                                    "modified_epoch": int(info.st_mtime),
                                                    "modified_ns": int(info.st_mtime_ns),
                                                    "age_days": max(0, int((now_value - info.st_mtime) // 86400)),
                                                    "status": "preview_only",
                                                    "reason": f"recognized browser cache file older than {minimum_age_days} day(s)",
                                                })
                                                if len(candidates) >= max_candidates:
                                                    scan_limited = True
                                                    stop = True
                                                    break
                                            except OSError:
                                                access_errors += 1
                                    if stop:
                                        break
                                except OSError:
                                    access_errors += 1
                    except OSError:
                        access_errors += 1
                    if stop:
                        break
        except OSError:
            access_errors += 1

    total_bytes = sum(item["size_bytes"] for item in candidates)
    partial = scan_limited or access_errors > 0
    return {
        **base, "status": "preview_partial" if partial else "preview_complete",
        "profiles_examined": profiles_examined, "cache_roots_examined": cache_roots_examined,
        "entries_examined": entries_examined, "profile_entries_examined": profile_entries_examined,
        "access_errors": access_errors, "unsafe_paths_skipped": unsafe_paths_skipped,
        "scan_limited": scan_limited, "candidate_count": len(candidates),
        "candidate_bytes": total_bytes, "candidate_mib": round(total_bytes / (1024 ** 2), 2),
        "candidates": candidates,
        "limitations": [
            "Preview only: no file is opened for content, moved, deleted, or changed.",
            "Only recognized Chrome, Edge, Brave, and Firefox cache directories are scanned.",
            "Cookies, browsing history, passwords, autofill, sessions, downloads, bookmarks, extensions, databases, and arbitrary app data are excluded.",
            "A cache file can be active or recreated by an open browser. This report is not authorization to remove anything.",
            "Partial results are not exhaustive. Close browsers and review exact paths before any future separately confirmed cleanup feature.",
            "The report includes local paths; inspect it before sharing publicly.",
        ],
        "note": "An empty result means no eligible old cache files were found within the recognized profiles and limits; it does not prove the whole PC is clean.",
    }
