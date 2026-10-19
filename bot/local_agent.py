"""On-demand local file access with explicit folder permissions and tamper-evident audit."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .application_cache import _is_link_or_reparse
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .file_lock import exclusive_file_lock

CONFIG_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 64 * 1024
MAX_FILE_BYTES = 200_000
MAX_DIRECTORY_ITEMS = 500
MAX_AUDIT_BYTES = 2 * 1024 * 1024
MAX_AUDIT_EVENTS = 5_000
MAX_JSON_DEPTH = 32
ZERO_SHA256 = "0" * 64
_ROOT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PERMISSIONS = frozenset({"list", "read", "write"})
_BLOCKED_COMPONENTS = frozenset({
    ".git", ".ssh", ".cerebro", ".venv", "venv", "node_modules",
    "__pycache__", ".idea", ".vscode",
})
_BLOCKED_NAMES = frozenset({
    ".env", ".env.local", ".env.production", ".netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519",
})
_BLOCKED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore"})
_AUDIT_FIELDS = frozenset({
    "sequence", "timestamp", "operation", "root_id", "path", "outcome",
    "detail", "byte_count", "previous_sha256", "sha256",
})


class LocalAgentError(ValueError):
    """Raised when local-agent configuration, policy, or I/O validation fails."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalAgentError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_link_components(path: str | Path, label: str, *, allow_missing: bool = True) -> None:
    """Reject symlinks/reparse points in all existing components of an absolute path."""
    current = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    while True:
        try:
            current.lstat()
        except FileNotFoundError:
            if not allow_missing:
                raise LocalAgentError(f"{label} does not exist")
        except OSError as exc:
            raise LocalAgentError(f"Could not safely inspect {label} path components") from exc
        else:
            if _is_link_or_reparse(current):
                raise LocalAgentError(f"{label} cannot include a symlink or Windows reparse point")
        parent = current.parent
        if parent == current:
            break
        current = parent


def _path_chain_signature(path: str | Path, label: str) -> tuple[tuple[Any, ...], ...]:
    """Capture existing path-component identities without following links.

    This is a best-effort race detector for path-based operations, not a
    substitute for handle-relative filesystem APIs or a security boundary
    against a hostile process running as the same user.
    """
    target = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    chain = [*reversed(target.parents), target]
    signature: list[tuple[Any, ...]] = []
    for component in chain:
        try:
            info = component.lstat()
        except OSError as exc:
            raise LocalAgentError(f"Could not safely inspect {label} path components") from exc
        if stat.S_ISLNK(info.st_mode) or _is_link_or_reparse(component):
            raise LocalAgentError(f"{label} cannot include a symlink or Windows reparse point")
        signature.append((
            os.path.normcase(str(component)),
            info.st_dev,
            info.st_ino,
            stat.S_IFMT(info.st_mode),
        ))
    return tuple(signature)


def _assert_path_chain_unchanged(
    path: str | Path,
    expected: tuple[tuple[Any, ...], ...],
    label: str,
) -> None:
    """Fail closed when any path component was replaced during an operation."""
    if _path_chain_signature(path, label) != expected:
        raise LocalAgentError(f"{label} path changed during operation; access blocked")


@contextmanager
def _directory_chain_lease(
    path: str | Path,
    expected_signature: tuple[tuple[Any, ...], ...],
    label: str,
):
    """Hold directory handles without FILE_SHARE_DELETE while a path is in use.

    On Windows, pin every existing directory component from the filesystem
    anchor through path. This prevents ordinary rename/reparse substitution
    of those directory components while path-based work is staged and published.
    The target file leaf is not pinned by this helper. This is defense in depth,
    not a hostile same-user sandbox.
    """
    target = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    initial = _path_chain_signature(target, label)
    if initial != expected_signature:
        raise LocalAgentError(f"{label} path changed before its directory lease; access blocked")

    if os.name != "nt":
        try:
            yield
        finally:
            _assert_path_chain_unchanged(target, initial, label)
        return

    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("volume_serial", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetFileInformationByHandle.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    file_read_attributes = 0x00000080
    share_read_write = 0x00000001 | 0x00000002  # deliberately omit FILE_SHARE_DELETE
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[int] = []
    chain = [*reversed(target.parents), target]

    try:
        for component in chain:
            before = component.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or _is_link_or_reparse(component)
            ):
                raise LocalAgentError(f"{label} contains a non-directory or reparse component")

            handle = kernel32.CreateFileW(
                os.fspath(component),
                file_read_attributes,
                share_read_write,
                None,
                open_existing,
                file_flag_open_reparse_point | file_flag_backup_semantics,
                None,
            )
            if handle is None or handle == invalid_handle:
                error = ctypes.get_last_error()
                raise OSError(error, ctypes.FormatError(error), os.fspath(component))
            handles.append(handle)

            info = _ByHandleFileInformation()
            if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                error = ctypes.get_last_error()
                raise OSError(error, ctypes.FormatError(error), os.fspath(component))
            if info.attributes & file_attribute_reparse_point:
                raise LocalAgentError(f"{label} contains a Windows reparse point")
            if not info.attributes & file_attribute_directory:
                raise LocalAgentError(f"{label} contains a component that is not a directory")

            native_index = (int(info.file_index_high) << 32) | int(info.file_index_low)
            after = component.lstat()
            if (
                int(info.volume_serial) != int(before.st_dev)
                or native_index != int(before.st_ino)
                or (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) != (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode))
                or _is_link_or_reparse(component)
            ):
                raise LocalAgentError(f"{label} component changed while its directory handle was opened")

        if _path_chain_signature(target, label) != initial:
            raise LocalAgentError(f"{label} path changed while directory handles were acquired")
        yield
        _assert_path_chain_unchanged(target, initial, label)
    except LocalAgentError:
        raise
    except OSError as exc:
        raise LocalAgentError(f"{label} directory lease failed: {type(exc).__name__}") from exc
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def _read_json_config(path: Path) -> Any:
    try:
        _reject_link_components(path, "Agent config", allow_missing=False)
        if not path.is_file():
            raise LocalAgentError("Agent config must be a regular JSON file")
        raw = read_bounded_bytes(path, limit_bytes=MAX_CONFIG_BYTES, label="Agent config")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        enforce_json_depth(value, max_depth=MAX_JSON_DEPTH, label="Agent config")
        return value
    except LocalAgentError:
        raise
    except (BoundedReadError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LocalAgentError(f"Agent config is invalid or unsafe: {type(exc).__name__}") from exc


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def load_local_agent_config(config_path: str | Path) -> dict[str, Any]:
    """Validate a user-authored config; this function does not create files."""
    lexical_config = Path(config_path).expanduser()
    _reject_link_components(lexical_config, "Agent config", allow_missing=False)
    config_file = lexical_config.resolve(strict=True)
    if not config_file.is_file():
        raise LocalAgentError("Agent config must be a regular file")
    payload = _read_json_config(config_file)
    if not isinstance(payload, dict):
        raise LocalAgentError("Agent config must be a JSON object")
    if set(payload) - {"schema_version", "roots", "audit_log"}:
        raise LocalAgentError("Agent config contains unsupported top-level fields")
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise LocalAgentError("Agent config schema_version must be 1")
    records = payload.get("roots")
    if not isinstance(records, list) or not 1 <= len(records) <= 20:
        raise LocalAgentError("Agent config roots must contain between 1 and 20 folder entries")

    roots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in records:
        if not isinstance(item, dict) or set(item) - {
            "id", "path", "permissions", "max_file_bytes"
        } or not {"id", "path", "permissions"} <= set(item):
            raise LocalAgentError("Each root requires only id, path, permissions, and optional max_file_bytes")
        root_id = item["id"]
        if not isinstance(root_id, str) or not _ROOT_ID.fullmatch(root_id):
            raise LocalAgentError("Root id must use 1–32 ASCII letters, digits, '_' or '-' and start with a letter")
        if root_id in seen_ids:
            raise LocalAgentError(f"Duplicate root id: {root_id}")
        seen_ids.add(root_id)
        raw_path = item["path"]
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise LocalAgentError(f"Root {root_id} path must be a non-empty string")
        requested_root = Path(raw_path).expanduser()
        if not requested_root.is_absolute():
            raise LocalAgentError(f"Root {root_id} path must be absolute")
        _reject_link_components(requested_root, f"Root {root_id}", allow_missing=False)
        try:
            root_path = requested_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise LocalAgentError(f"Root {root_id} cannot be resolved safely") from exc
        if not root_path.is_dir():
            raise LocalAgentError(f"Root {root_id} must be an existing directory")
        if root_path.name.casefold() in _BLOCKED_COMPONENTS:
            raise LocalAgentError(f"Root {root_id} points to a directory protected by the baseline policy")
        permissions = item["permissions"]
        if (
            not isinstance(permissions, list) or not permissions
            or any(not isinstance(permission, str) for permission in permissions)
            or len(set(permissions)) != len(permissions)
            or set(permissions) - _PERMISSIONS
        ):
            raise LocalAgentError(f"Root {root_id} permissions must be a non-empty unique list chosen from list/read/write")
        limit = item.get("max_file_bytes", MAX_FILE_BYTES)
        if (
            not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= MAX_FILE_BYTES
        ):
            raise LocalAgentError(f"Root {root_id} max_file_bytes must be an integer from 1 to {MAX_FILE_BYTES}")
        roots.append({
            "id": root_id,
            "path": str(root_path),
            "permissions": sorted(permissions),
            "max_file_bytes": limit,
        })

    for root in roots:
        if _inside(config_file, Path(root["path"])):
            raise LocalAgentError("Agent config must be outside every configured root")
    for index, root in enumerate(roots):
        current = Path(root["path"])
        for other in roots[:index]:
            other_path = Path(other["path"])
            if _inside(current, other_path) or _inside(other_path, current):
                raise LocalAgentError(
                    f"Configured roots cannot overlap: {root['id']} and {other['id']}"
                )

    raw_audit = payload.get("audit_log")
    if raw_audit is None:
        audit_input = config_file.parent / "local-agent-audit.jsonl"
    elif isinstance(raw_audit, str) and raw_audit.strip():
        audit_input = Path(raw_audit).expanduser()
        if not audit_input.is_absolute():
            audit_input = config_file.parent / audit_input
    else:
        raise LocalAgentError("audit_log must be a non-empty path string when supplied")

    _reject_link_components(audit_input, "Audit log", allow_missing=True)
    audit_path = audit_input.resolve(strict=False)
    audit_parent = audit_path.parent
    if not audit_parent.is_dir():
        raise LocalAgentError("Audit log parent directory must already exist")
    _reject_link_components(audit_parent, "Audit log parent", allow_missing=False)
    if audit_path.exists():
        _reject_link_components(audit_path, "Audit log", allow_missing=False)
        if not audit_path.is_file():
            raise LocalAgentError("Audit log must be a regular file")
    for root in roots:
        if _inside(audit_path, Path(root["path"])):
            raise LocalAgentError(
                "Audit log must be outside every configured root so the agent cannot modify its own audit"
            )
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "config_path": str(config_file),
        "audit_path": str(audit_path),
        "roots": roots,
    }


def _canonical_relative_path(raw: str, *, allow_root: bool = False) -> str:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        raise LocalAgentError("Path must be a non-empty canonical relative path")
    if raw == "." and allow_root:
        return raw
    if (
        raw in {".", ".."} or raw.startswith("/")
        or "\\" in raw or ":" in raw or "\x00" in raw
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise LocalAgentError("Path must be relative and cannot contain traversal, drive, stream, or control syntax")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != raw:
        raise LocalAgentError("Path is not canonical or attempts parent traversal")
    for part in candidate.parts:
        if part in {"", ".", ".."} or part.endswith((".", " ")):
            raise LocalAgentError("Path contains an unsupported component")
        device_base = part.split(".", 1)[0].upper()
        if device_base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", device_base):
            raise LocalAgentError("Windows reserved device names are not supported")
    return candidate.as_posix()


def _is_protected(relative: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    if any(part in _BLOCKED_COMPONENTS for part in parts):
        return True
    name = parts[-1] if parts else ""
    return (
        name in _BLOCKED_NAMES
        or name.startswith(".env.")
        or Path(name).suffix.lower() in _BLOCKED_SUFFIXES
    )


def _resolve_under_root(root: Path, relative: str, *, allow_missing_leaf: bool = False) -> Path:
    """Resolve one non-recursive relative path while rejecting link/reparse components."""
    _reject_link_components(root, "Authorized root", allow_missing=False)
    current = root
    parts = [] if relative == "." else list(PurePosixPath(relative).parts)
    for index, part in enumerate(parts):
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf and index == len(parts) - 1:
                continue
            raise LocalAgentError("Requested path does not exist")
        except OSError as exc:
            raise LocalAgentError("Could not safely inspect requested path") from exc
        if _is_link_or_reparse(current):
            raise LocalAgentError("Symlinks and Windows reparse points are not permitted")
        if index < len(parts) - 1 and not current.is_dir():
            raise LocalAgentError("An intermediate path component is not a directory")
    try:
        resolved = current.resolve(strict=not allow_missing_leaf or current.exists())
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalAgentError("Requested path escapes the configured root or cannot be resolved safely") from exc
    return resolved


class LocalAgent:
    """Explicit, bounded list/read/write operations with a verified hash-chain audit."""

    def __init__(self, config_path: str | Path):
        self.config = load_local_agent_config(config_path)
        self.audit_path = Path(self.config["audit_path"])
        self.roots = {
            item["id"]: {
                **item,
                "path_object": Path(item["path"]),
                "permission_set": frozenset(item["permissions"]),
            }
            for item in self.config["roots"]
        }

    def config_summary(self) -> dict[str, Any]:
        return {
            "status": "config_valid",
            "schema_version": CONFIG_SCHEMA_VERSION,
            "config_path": self.config["config_path"],
            "audit_path": str(self.audit_path),
            "roots": [
                {
                    "id": root["id"], "path": root["path"],
                    "permissions": root["permissions"],
                    "max_file_bytes": root["max_file_bytes"],
                }
                for root in self.config["roots"]
            ],
            "files_written": False,
            "network_used": False,
            "ai_enabled": False,
        }

    def _load_audit_unlocked(self) -> list[dict[str, Any]]:
        _reject_link_components(self.audit_path, "Audit log", allow_missing=True)
        if not self.audit_path.exists():
            return []
        try:
            info = self.audit_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_AUDIT_BYTES:
                raise LocalAgentError("Audit log is not a regular file or exceeds its size limit")
            raw = read_bounded_bytes(
                self.audit_path, limit_bytes=MAX_AUDIT_BYTES, label="Audit log"
            )
        except LocalAgentError:
            raise
        except (BoundedReadError, OSError) as exc:
            raise LocalAgentError("Could not read audit log safely") from exc
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise LocalAgentError("Audit log is incomplete; operations are blocked until reviewed")
        records: list[dict[str, Any]] = []
        previous = ZERO_SHA256
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line or len(records) >= MAX_AUDIT_EVENTS:
                raise LocalAgentError("Audit log contains an empty or excessive event record")
            try:
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
            except (UnicodeError, json.JSONDecodeError, LocalAgentError, RecursionError) as exc:
                raise LocalAgentError(f"Audit log record {line_number} is not valid JSON") from exc
            if not isinstance(record, dict) or set(record) != _AUDIT_FIELDS:
                raise LocalAgentError(f"Audit log record {line_number} has an invalid schema")
            if record.get("sequence") != line_number:
                raise LocalAgentError(f"Audit log record {line_number} has an invalid sequence")
            if not isinstance(record.get("timestamp"), str) or not record["timestamp"]:
                raise LocalAgentError(f"Audit log record {line_number} has an invalid timestamp")
            if record.get("operation") not in {"list", "read", "write"}:
                raise LocalAgentError(f"Audit log record {line_number} has an invalid operation")
            if record.get("outcome") not in {"intent", "success", "denied", "error"}:
                raise LocalAgentError(f"Audit log record {line_number} has an invalid outcome")
            if record.get("root_id") is not None and not isinstance(record["root_id"], str):
                raise LocalAgentError(f"Audit log record {line_number} has an invalid root ID")
            if record.get("path") is not None and not isinstance(record["path"], str):
                raise LocalAgentError(f"Audit log record {line_number} has an invalid path")
            if not isinstance(record.get("detail"), str):
                raise LocalAgentError(f"Audit log record {line_number} has invalid detail")
            count = record.get("byte_count")
            if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
                raise LocalAgentError(f"Audit log record {line_number} has an invalid byte count")
            if record.get("previous_sha256") != previous:
                raise LocalAgentError(f"Audit log hash chain breaks at record {line_number}")
            digest = record.get("sha256")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise LocalAgentError(f"Audit log record {line_number} has an invalid hash")
            unsigned = {key: value for key, value in record.items() if key != "sha256"}
            if _sha256(_canonical_json(unsigned)) != digest:
                raise LocalAgentError(f"Audit log record {line_number} failed hash verification")
            previous = digest
            records.append(record)
        return records

    def verify_audit(self) -> dict[str, Any]:
        try:
            # Verification is read-only: do not create a sidecar lock just to inspect the log.
            # A concurrent append may yield a transient incomplete-chain result, never a write.
            records = self._load_audit_unlocked()
            return {
                "status": "valid",
                "event_count": len(records),
                "last_sha256": records[-1]["sha256"] if records else ZERO_SHA256,
                "audit_path": str(self.audit_path),
                "network_used": False,
                "ai_enabled": False,
            }
        except LocalAgentError:
            raise
        except OSError as exc:
            raise LocalAgentError("Audit verification could not safely acquire the file lock") from exc

    def _record(
        self, operation: str, root_id: str | None, relative: str | None,
        outcome: str, detail: str, byte_count: int | None = None,
    ) -> dict[str, Any]:
        lock = self.audit_path.with_name(f".{self.audit_path.name}.lock")
        try:
            with exclusive_file_lock(lock):
                records = self._load_audit_unlocked()
                event_reserve = 1 if outcome == "intent" else 0
                if len(records) >= MAX_AUDIT_EVENTS - event_reserve:
                    raise LocalAgentError("Audit event limit reached; no new operation is allowed")
                event = {
                    "sequence": len(records) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "operation": operation,
                    "root_id": root_id[:32] if isinstance(root_id, str) else None,
                    "path": relative[:512] if isinstance(relative, str) else None,
                    "outcome": outcome,
                    "detail": detail[:120],
                    "byte_count": byte_count,
                    "previous_sha256": records[-1]["sha256"] if records else ZERO_SHA256,
                }
                record = {**event, "sha256": _sha256(_canonical_json(event))}
                encoded = _canonical_json(record) + b"\n"
                current_size = self.audit_path.stat().st_size if self.audit_path.exists() else 0
                reserve = 1024 if outcome == "intent" else 0
                if current_size + len(encoded) + reserve > MAX_AUDIT_BYTES:
                    raise LocalAgentError("Audit byte limit reached; no new operation is allowed")
                _reject_link_components(self.audit_path.parent, "Audit parent", allow_missing=False)
                if self.audit_path.exists():
                    _reject_link_components(self.audit_path, "Audit log", allow_missing=False)
                    before = self.audit_path.lstat()
                    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                    descriptor = os.open(self.audit_path, flags)
                else:
                    before = None
                    flags = (
                        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                    )
                    descriptor = os.open(self.audit_path, flags, 0o600)
                try:
                    opened = os.fstat(descriptor)
                    current = self.audit_path.lstat()
                    if (
                        not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
                        or _is_link_or_reparse(self.audit_path)
                        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                        or (before is not None and (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino))
                    ):
                        raise LocalAgentError("Audit log identity changed; operation blocked")
                    offset = 0
                    while offset < len(encoded):
                        offset += os.write(descriptor, encoded[offset:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return record
        except LocalAgentError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise LocalAgentError(f"Audit write failed; requested action was not started: {type(exc).__name__}") from exc

    def _authorize(self, operation: str, root_id: str, raw_path: str, permission: str, *, allow_root: bool = False) -> tuple[dict[str, Any], str]:
        root = self.roots.get(root_id) if isinstance(root_id, str) else None
        relative: str | None = None
        try:
            relative = _canonical_relative_path(raw_path, allow_root=allow_root)
        except LocalAgentError:
            self._record(operation, root_id if isinstance(root_id, str) else None, None, "denied", "invalid_relative_path")
            raise
        if _is_protected(relative):
            self._record(operation, root_id, relative, "denied", "protected_path")
            raise LocalAgentError("This path is protected by the local-agent baseline policy")
        if root is None:
            self._record(operation, root_id if isinstance(root_id, str) else None, relative, "denied", "unknown_root")
            raise LocalAgentError(f"Unknown local-agent root id: {root_id}")
        if permission not in root["permission_set"]:
            self._record(operation, root_id, relative, "denied", "permission_not_granted")
            raise LocalAgentError(f"Root {root_id} does not grant {permission!r} permission")
        return root, relative

    def _resolve_authorized(
        self, operation: str, root: dict[str, Any], relative: str, *,
        allow_missing_leaf: bool = False,
    ) -> Path:
        try:
            return _resolve_under_root(
                root["path_object"], relative, allow_missing_leaf=allow_missing_leaf
            )
        except LocalAgentError:
            self._record(operation, root["id"], relative, "denied", "path_resolution_failed")
            raise

    def list_directory(self, root_id: str, relative_path: str = ".") -> dict[str, Any]:
        root, relative = self._authorize("list", root_id, relative_path, "list", allow_root=True)
        target = self._resolve_authorized("list", root, relative)
        if not target.is_dir():
            self._record("list", root_id, relative, "denied", "not_a_directory")
            raise LocalAgentError("Requested path is not a directory")
        target_signature = _path_chain_signature(target, "Directory")
        self._record("list", root_id, relative, "intent", "directory_listing_started")
        items: list[dict[str, Any]] = []
        skipped_links = 0
        truncated = False
        try:
            with _directory_chain_lease(target, target_signature, "Directory"):
                _reject_link_components(target, "Directory", allow_missing=False)
                with os.scandir(target) as entries:
                    for index, entry in enumerate(entries):
                        if index >= MAX_DIRECTORY_ITEMS:
                            truncated = True
                            break
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if entry.is_symlink() or _is_link_or_reparse(Path(entry.path)):
                                skipped_links += 1
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                kind = "directory"
                                size = None
                            elif stat.S_ISREG(info.st_mode):
                                kind = "file"
                                size = info.st_size
                            else:
                                skipped_links += 1
                                continue
                            items.append({"name": entry.name, "type": kind, "size_bytes": size})
                        except OSError:
                            skipped_links += 1
            _assert_path_chain_unchanged(target, target_signature, "Directory")
            items.sort(key=lambda item: item["name"].casefold())
            self._record("list", root_id, relative, "success", "directory_listing_completed", len(items))
            return {
                "status": "completed", "root_id": root_id, "relative_path": relative,
                "items": items, "returned_count": len(items), "skipped_entries": skipped_links,
                "truncated": truncated, "read_only": True, "files_written": False,
                "network_used": False, "ai_enabled": False,
            }
        except Exception as exc:
            try:
                self._record("list", root_id, relative, "error", type(exc).__name__)
            except LocalAgentError:
                pass
            if isinstance(exc, LocalAgentError):
                raise
            raise LocalAgentError(f"Directory listing failed: {type(exc).__name__}") from exc

    def read_file(self, root_id: str, relative_path: str) -> dict[str, Any]:
        root, relative = self._authorize("read", root_id, relative_path, "read")
        target = self._resolve_authorized("read", root, relative)
        if not target.is_file():
            self._record("read", root_id, relative, "denied", "not_a_regular_file")
            raise LocalAgentError("Requested path is not a regular file")
        _reject_link_components(target, "Read target", allow_missing=False)
        if target.stat().st_size > root["max_file_bytes"]:
            self._record("read", root_id, relative, "denied", "file_exceeds_limit")
            raise LocalAgentError("File exceeds the configured read limit")
        self._record("read", root_id, relative, "intent", "file_read_started")
        try:
            _reject_link_components(target, "Read target", allow_missing=False)
            path_signature = _path_chain_signature(target, "Read target")
            raw = read_bounded_bytes(
                target, limit_bytes=root["max_file_bytes"], label="Local-agent file"
            )
            _assert_path_chain_unchanged(target, path_signature, "Read target")
            content = raw.decode("utf-8")
            self._record("read", root_id, relative, "success", f"sha256={_sha256(raw)}", len(raw))
            return {
                "status": "completed", "root_id": root_id, "relative_path": relative,
                "content": content, "size_bytes": len(raw), "sha256": _sha256(raw),
                "files_written": False, "network_used": False, "ai_enabled": False,
            }
        except Exception as exc:
            try:
                self._record("read", root_id, relative, "error", type(exc).__name__)
            except LocalAgentError:
                pass
            if isinstance(exc, LocalAgentError):
                raise
            raise LocalAgentError(f"File read failed: {type(exc).__name__}") from exc

    def write_file(
        self, root_id: str, relative_path: str, content: str, *,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        root, relative = self._authorize("write", root_id, relative_path, "write")
        if not isinstance(content, str):
            self._record("write", root_id, relative, "denied", "content_not_text")
            raise LocalAgentError("Write content must be UTF-8 text")
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            self._record("write", root_id, relative, "denied", "content_not_valid_unicode")
            raise LocalAgentError("Write content must be valid UTF-8 text") from exc
        if len(raw) > root["max_file_bytes"]:
            self._record("write", root_id, relative, "denied", "content_exceeds_limit")
            raise LocalAgentError("Write content exceeds the configured limit")
        target = self._resolve_authorized(
            "write", root, relative, allow_missing_leaf=True
        )
        parent = target.parent
        if not parent.is_dir():
            self._record("write", root_id, relative, "denied", "parent_not_directory")
            raise LocalAgentError("Parent directory must already exist; the agent never creates directories")
        _reject_link_components(parent, "Write parent", allow_missing=False)
        parent_signature = _path_chain_signature(parent, "Write parent")
        exists = target.exists()
        target_signature: tuple[tuple[Any, ...], ...] | None = None
        if exists:
            if not target.is_file():
                self._record("write", root_id, relative, "denied", "target_not_regular_file")
                raise LocalAgentError("Existing write target must be a regular file")
            if "read" not in root["permission_set"]:
                self._record("write", root_id, relative, "denied", "read_permission_required_to_replace")
                raise LocalAgentError("Replacing an existing file also requires read permission and its exact SHA-256")
            _reject_link_components(target, "Write target", allow_missing=False)
            try:
                original = read_bounded_bytes(
                    target, limit_bytes=root["max_file_bytes"], label="Existing write target"
                )
            except (BoundedReadError, OSError) as exc:
                self._record("write", root_id, relative, "denied", "existing_target_unreadable")
                raise LocalAgentError("Existing target cannot be safely checked") from exc
            current_hash = _sha256(original)
            if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256) or expected_sha256 != current_hash:
                self._record("write", root_id, relative, "denied", "expected_sha256_mismatch")
                raise LocalAgentError("Replacing an existing file requires its exact current expected_sha256")
            target_signature = _path_chain_signature(target, "Write target")
        else:
            if expected_sha256 is not None:
                self._record("write", root_id, relative, "denied", "hash_supplied_for_missing_target")
                raise LocalAgentError("expected_sha256 must be omitted when creating a new file")
            current_hash = None

        temporary: str | None = None
        try:
            with _directory_chain_lease(parent, parent_signature, "Write parent"):
                self._record("write", root_id, relative, "intent", f"sha256={_sha256(raw)}", len(raw))
                # Revalidate path components and the file identity immediately before publish.
                _reject_link_components(parent, "Write parent", allow_missing=False)
                current_target = _resolve_under_root(
                    root["path_object"], relative, allow_missing_leaf=True
                )
                if current_target != target:
                    raise LocalAgentError("Write target path changed during the operation")
                if exists:
                    _reject_link_components(target, "Write target", allow_missing=False)
                    if target_signature is None:
                        raise LocalAgentError("Write target identity was not captured; replacement blocked")
                    _assert_path_chain_unchanged(target, target_signature, "Write target")
                    if not target.is_file() or _sha256(read_bounded_bytes(
                        target, limit_bytes=root["max_file_bytes"], label="Existing write target"
                    )) != current_hash:
                        raise LocalAgentError("Write target changed after authorization; no replacement was made")
                elif target.exists():
                    raise LocalAgentError("A file appeared at the new destination; it was preserved")
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{target.name}.", suffix=".botpyton-agent-tmp", dir=parent
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                staged = Path(temporary)
                _reject_link_components(staged, "Staged write", allow_missing=False)
                if not staged.is_file() or staged.stat().st_size != len(raw):
                    raise LocalAgentError("Staged write failed regular-file or size verification")
                if _sha256(read_bounded_bytes(
                    staged, limit_bytes=root["max_file_bytes"], label="Staged write"
                )) != _sha256(raw):
                    raise LocalAgentError("Staged write content verification failed")
                _assert_path_chain_unchanged(parent, parent_signature, "Write parent")
                _reject_link_components(parent, "Write parent", allow_missing=False)
                _reject_link_components(target, "Write target", allow_missing=True)
                _assert_path_chain_unchanged(parent, parent_signature, "Write parent")
                if exists:
                    _reject_link_components(target, "Write target", allow_missing=False)
                    if target_signature is None:
                        raise LocalAgentError("Write target identity was not captured; replacement blocked")
                    _assert_path_chain_unchanged(target, target_signature, "Write target")
                    if not target.is_file() or _sha256(read_bounded_bytes(
                        target, limit_bytes=root["max_file_bytes"], label="Existing write target"
                    )) != current_hash:
                        raise LocalAgentError("Write target changed before publication; no replacement was made")
                    _assert_path_chain_unchanged(parent, parent_signature, "Write parent")
                    _assert_path_chain_unchanged(target, target_signature, "Write target")
                    os.replace(temporary, target)
                    temporary = None
                else:
                    # Hard-link publication is exclusive: a competing destination is never overwritten.
                    os.link(temporary, target)
                    os.unlink(temporary)
                    temporary = None

                _assert_path_chain_unchanged(parent, parent_signature, "Write parent")
                _reject_link_components(target, "Published write", allow_missing=False)
                if not target.is_file():
                    raise LocalAgentError("Published write is not a regular file")
                published = read_bounded_bytes(
                    target, limit_bytes=root["max_file_bytes"], label="Published write"
                )
                if _sha256(published) != _sha256(raw):
                    raise LocalAgentError("Published file content verification failed")
                self._record("write", root_id, relative, "success", f"sha256={_sha256(raw)}", len(raw))
                return {
                    "status": "completed", "root_id": root_id, "relative_path": relative,
                    "created": not exists, "size_bytes": len(raw), "sha256": _sha256(raw),
                    "files_written": True, "backup_created": False,
                    "network_used": False, "ai_enabled": False,
                    "note": "Existing files require matching expected_sha256. Directory and delete operations are not supported.",
                }
        except Exception as exc:
            try:
                self._record("write", root_id, relative, "error", type(exc).__name__)
            except LocalAgentError:
                pass
            if isinstance(exc, LocalAgentError):
                raise
            raise LocalAgentError(f"File write failed: {type(exc).__name__}") from exc
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
