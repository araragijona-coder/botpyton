"""Read-only analysis of Windows startup targets; never executes or disables entries."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

MAX_STARTUP_ADVISOR_ITEMS = 200
MAX_STARTUP_COMMAND_CHARS = 4096
_EXECUTABLE_SUFFIX = re.compile(r"\.(?:exe|com|bat|cmd|ps1|vbs|js)$", re.IGNORECASE)
_UNQUOTED_TARGET = re.compile(r"^(.+?\.(?:exe|com|bat|cmd|ps1|vbs|js))(?=\s|$)", re.IGNORECASE)
_ENV_VAR = re.compile(r"%([^%]+)%")


def _extract_executable_token(command: object) -> str | None:
    """Return only the executable/script token, never command arguments."""
    if not isinstance(command, str) or not command or len(command) > MAX_STARTUP_COMMAND_CHARS:
        return None
    value = command.strip()
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        if end <= 1:
            return None
        token = value[1:end]
    else:
        match = _UNQUOTED_TARGET.match(value)
        if not match:
            return None
        token = match.group(1).strip()
    return token if _EXECUTABLE_SUFFIX.search(token) else None


def _expand_windows_environment(value: str, environ: dict[str, str]) -> str:
    """Expand only explicit %NAME% references using the supplied environment."""
    folded = {key.casefold(): item for key, item in environ.items()}
    return _ENV_VAR.sub(lambda match: folded.get(match.group(1).casefold(), match.group(0)), value)


def _path_state(
    raw_command: object,
    *,
    environ: dict[str, str],
    is_file: Callable[[str], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> tuple[str | None, str]:
    token = _extract_executable_token(raw_command)
    if token is None:
        return None, "unresolved"
    expanded = _expand_windows_environment(token, environ)
    finder = shutil.which if which is None else which
    if not Path(expanded).is_absolute() and "/" not in expanded and "\\" not in expanded:
        located = finder(expanded)
        if located:
            expanded = located
    exists = (lambda value: Path(value).is_file()) if is_file is None else is_file
    try:
        present = bool(exists(expanded))
    except (OSError, ValueError, RuntimeError):
        return expanded[:1024], "unresolved"
    return expanded[:1024], "target_present" if present else "target_missing"


def read_startup_advisor(
    *,
    windows: bool | None = None,
    registry_module: object | None = None,
    environ: dict[str, str] | None = None,
    is_file: Callable[[str], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict:
    """Read bounded startup metadata and check targets; no commands are executed."""
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return {
            "status": "unsupported_platform", "items": [], "items_count": 0,
            "read_only": True, "network_used": False,
            "note": "The startup target advisor is supported only on Windows.",
        }
    if registry_module is None:
        try:
            import winreg as registry_module
        except ImportError:
            return {
                "status": "unavailable", "items": [], "items_count": 0,
                "read_only": True, "network_used": False,
                "note": "Windows registry access is unavailable in this Python runtime.",
            }

    env = dict(os.environ if environ is None else environ)
    registry = registry_module
    locations = (
        (registry.HKEY_CURRENT_USER, "HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (registry.HKEY_CURRENT_USER, "HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        (registry.HKEY_LOCAL_MACHINE, "HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (registry.HKEY_LOCAL_MACHINE, "HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    )
    items: list[dict] = []
    access_errors = 0
    scan_limited = False

    def append_entry(name: str, source: str, command: object, kind: str) -> bool:
        nonlocal scan_limited
        if len(items) >= MAX_STARTUP_ADVISOR_ITEMS:
            scan_limited = True
            return False
        target, state = _path_state(command, environ=env, is_file=is_file, which=which)
        items.append({
            "name": str(name)[:160],
            "source": source[:200],
            "kind": kind,
            "target_path": target,
            "target_status": state,
            "command_arguments_captured": False,
        })
        return True

    for hive, hive_name, subkey in locations:
        if len(items) >= MAX_STARTUP_ADVISOR_ITEMS:
            scan_limited = True
            break
        try:
            with registry.OpenKey(hive, subkey, 0, registry.KEY_READ) as key:
                count = int(registry.QueryInfoKey(key)[1])
                remaining = MAX_STARTUP_ADVISOR_ITEMS - len(items)
                if count > remaining:
                    scan_limited = True
                for index in range(min(count, remaining)):
                    try:
                        name, command, _kind = registry.EnumValue(key, index)
                        if not append_entry(name, f"{hive_name}\\{subkey}", command, "registry_run"):
                            break
                    except OSError:
                        access_errors += 1
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {2, 3} and getattr(exc, "errno", None) not in {2, 3}:
                access_errors += 1

    startup_folders = (
        ("current-user startup folder", Path(env["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" if env.get("APPDATA") else None),
        ("all-users startup folder", Path(env["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs/StartUp" if env.get("PROGRAMDATA") else None),
    )
    for source, folder in startup_folders:
        if folder is None or len(items) >= MAX_STARTUP_ADVISOR_ITEMS:
            continue
        try:
            if not folder.is_dir() or folder.is_symlink():
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if len(items) >= MAX_STARTUP_ADVISOR_ITEMS:
                        scan_limited = True
                        break
                    try:
                        path = Path(entry.path)
                        if entry.is_symlink() or path.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        if path.suffix.casefold() == ".lnk":
                            items.append({
                                "name": entry.name[:160], "source": source,
                                "kind": "startup_shortcut", "target_path": str(path)[:1024],
                                "target_status": "shortcut_target_unresolved",
                                "command_arguments_captured": False,
                            })
                        else:
                            target, state = _path_state(str(path), environ=env, is_file=is_file, which=which)
                            items.append({
                                "name": entry.name[:160], "source": source,
                                "kind": "startup_file", "target_path": target,
                                "target_status": state, "command_arguments_captured": False,
                            })
                    except OSError:
                        access_errors += 1
        except OSError:
            access_errors += 1

    status = "partial" if scan_limited or access_errors else "collected"
    return {
        "schema_version": 1, "status": status, "items": items,
        "items_count": len(items), "scan_limited": scan_limited,
        "access_errors": access_errors, "read_only": True,
        "network_used": False, "ai_enabled": False,
        "operations_launched": 0, "files_written": 0,
        "settings_changed": False, "startup_entries_disabled": 0,
        "note": (
            "This is target-path evidence, not a boot-impact measurement or a recommendation to disable an entry. "
            "Registry command arguments are never returned. Shortcut targets are not resolved. "
            "Review publisher and purpose before any manual change; the report may contain local paths."
        ),
    }
