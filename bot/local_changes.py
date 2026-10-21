"""Apply explicit local file-change plans with hash checks, backups and rollback."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .application_cache import _is_link_or_reparse
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .file_lock import exclusive_file_lock

MAX_FILES = 20
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 500_000
MAX_PLAN_BYTES = 600_000
MAX_TRANSACTION_BYTES = 2 * 1024 * 1024
TRANSACTION_SCHEMA_VERSION = 2
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PROTECTED_PREFIXES = (
    ".git/", ".github/", ".cerebro/policies/", ".cerebro/memory/",
    ".cerebro/reports/", ".cerebro/backups/", ".venv/", "node_modules/",
    "dist/", "build/",
)
PROTECTED_EXACT = frozenset({".cerebro/config.json"})
_PROTECTED_PREFIXES_CASEFOLD = tuple(prefix.casefold() for prefix in PROTECTED_PREFIXES)
_PROTECTED_EXACT_CASEFOLD = frozenset(path.casefold() for path in PROTECTED_EXACT)
ALLOWED_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".java", ".kt", ".kts", ".go", ".rs", ".rb", ".php",
    ".swift", ".m", ".mm", ".html", ".css", ".scss", ".vue", ".svelte", ".sh",
    ".ps1", ".bat", ".cmd", ".sql", ".json", ".yml", ".yaml", ".toml", ".md",
    ".txt", ".ini", ".cfg", ".xml", ".gradle", ".kts", ".properties", ".svg",
})


class LocalChangeError(ValueError):
    """Raised when a local change plan is invalid or cannot be applied safely."""


class _PublishedCleanupError(LocalChangeError):
    """A destination was published, but its temporary staging alias could not be removed."""

    def __init__(self, path: Path, temporary_path: Path, sha256: str):
        super().__init__("Staging alias cleanup failed after publication")
        self.path = path
        self.temporary_path = temporary_path
        self.sha256 = sha256


def _reject_link_components(path: str | Path, label: str) -> None:
    """Fail closed when any existing path component is a symlink/reparse point."""
    current = Path(os.path.abspath(str(Path(path).expanduser())))
    while True:
        try:
            current.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise LocalChangeError(f"Could not safely inspect {label} path components") from exc
        else:
            if _is_link_or_reparse(current):
                raise LocalChangeError(f"{label} contains a symlink or Windows reparse-point component")
        parent = current.parent
        if parent == current:
            break
        current = parent


def _safe_relative_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        raise LocalChangeError("Every path must be a non-empty canonical relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw in {".", ".."} or ".." in path.parts or "\\" in raw:
        raise LocalChangeError(f"Unsafe relative path: {raw!r}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise LocalChangeError("Paths cannot contain control characters")
    normalized = path.as_posix()
    if normalized != raw:
        raise LocalChangeError(f"Path is not canonical: {raw!r}")
    folded = normalized.casefold()
    if folded in _PROTECTED_EXACT_CASEFOLD or any(
        folded.startswith(prefix) for prefix in _PROTECTED_PREFIXES_CASEFOLD
    ):
        raise LocalChangeError(f"Protected path cannot be modified: {normalized}")
    if not any(normalized.lower().endswith(suffix) for suffix in ALLOWED_SUFFIXES):
        raise LocalChangeError(f"File type is not approved for automatic changes: {normalized}")
    return normalized


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _resolve_target(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise LocalChangeError(f"Could not safely inspect path component: {relative}") from exc
        if _is_link_or_reparse(current):
            raise LocalChangeError(f"Symlink or reparse-point path components are not allowed: {relative}")
    resolved = current.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LocalChangeError(f"Target escapes project root: {relative}") from exc
    return resolved


def _validate_local_change_plan(plan: Any, root: str | Path) -> dict:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise LocalChangeError("Plan must be a JSON object with schema_version=1")
    task_id = plan.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise LocalChangeError("task_id must be 1–64 safe ASCII letters, digits, '.', '_' or '-'")
    changes = plan.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_FILES:
        raise LocalChangeError(f"changes must contain 1–{MAX_FILES} file entries")
    lexical_root = Path(root).expanduser()
    _reject_link_components(lexical_root, "Project root")
    base = lexical_root.resolve()
    if not base.is_dir():
        raise LocalChangeError("Project root must be an existing directory")
    seen: set[str] = set()
    normalized = []
    total_bytes = 0
    for item in changes:
        if not isinstance(item, dict) or set(item) - {"path", "content", "expected_sha256"} or not {"path", "content"} <= item.keys():
            raise LocalChangeError("Each change requires path and content, with optional expected_sha256")
        relative = _safe_relative_path(item["path"])
        if relative in seen:
            raise LocalChangeError(f"Duplicate path: {relative}")
        seen.add(relative)
        content = item["content"]
        if not isinstance(content, str):
            raise LocalChangeError(f"Content must be UTF-8 text: {relative}")
        raw = content.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise LocalChangeError(f"File exceeds {MAX_FILE_BYTES} bytes: {relative}")
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            raise LocalChangeError(f"Plan exceeds {MAX_TOTAL_BYTES} bytes total")
        expected = item.get("expected_sha256")
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise LocalChangeError(f"expected_sha256 must be a lowercase SHA-256 digest: {relative}")
        target = _resolve_target(base, relative)
        exists = target.exists()
        current_hash = None
        if exists:
            if not target.is_file():
                raise LocalChangeError(f"Target exists but is not a regular file: {relative}")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise LocalChangeError(f"Existing file exceeds {MAX_FILE_BYTES} bytes: {relative}")
            current_hash = _sha256(target.read_bytes())
            if expected is None:
                raise LocalChangeError(f"Existing file requires expected_sha256 to prevent accidental overwrite: {relative}")
            if current_hash != expected:
                raise LocalChangeError(f"File changed since plan was prepared: {relative}")
        elif expected is not None:
            raise LocalChangeError(f"expected_sha256 was provided but file does not exist: {relative}")
        normalized.append({
            "path": relative, "content": content, "expected_sha256": expected,
            "exists": exists, "current_sha256": current_hash, "new_sha256": _sha256(raw),
            "bytes": len(raw),
        })
    return {
        "schema_version": 1,
        "task_id": task_id,
        "root": str(base),
        "changes": sorted(normalized, key=lambda item: item["path"]),
        "file_count": len(normalized),
        "total_bytes": total_bytes,
        "mode": "validated_dry_run",
        "writes_performed": False,
        "network_used": False,
        "ai_enabled": False,
    }


def _public_summary(validated: dict) -> dict:
    """Remove file contents before returning plans to CLI output or durable reports."""
    return {
        **validated,
        "changes": [
            {key: value for key, value in item.items() if key != "content"}
            for item in validated["changes"]
        ],
    }


def validate_local_change_plan(plan: Any, root: str | Path) -> dict:
    """Validate a plan and return a content-free summary suitable for display."""
    return _public_summary(_validate_local_change_plan(plan, root))


def _write_atomic(
    path: Path,
    raw: bytes,
    *,
    replace_existing: bool = True,
    expected_current_sha256: str | None = None,
) -> None:
    """Publish a complete file atomically; optionally require an unused destination."""
    _reject_link_components(path.parent, "Write destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(path.parent, "Write destination")
    _reject_link_components(path, "Write destination")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".botpyton-tmp", dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck path components just before publication. For new files, hard-link
        # publication is exclusive: a file created concurrently is never replaced.
        _reject_link_components(path.parent, "Write destination")
        _reject_link_components(path, "Write destination")
        if replace_existing:
            if expected_current_sha256 is not None:
                _reject_link_components(path, "Write destination")
                if _sha256(path.read_bytes()) != expected_current_sha256:
                    raise OSError("write aborted because destination content changed before publication")
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        published = True
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError as exc:
                if published:
                    raise _PublishedCleanupError(path, Path(temporary), _sha256(raw)) from exc
                raise


def apply_local_change_plan(
    plan: Any,
    root: str | Path,
    *,
    backup_root: str | Path | None = None,
) -> dict:
    """Apply a reviewed change plan with backups, a durable journal, and guarded rollback."""
    from .local_change_journal import (
        _persist_transaction_journal, _transaction_identity,
        _transaction_journal_path, _transaction_timestamp,
    )
    validated = _validate_local_change_plan(plan, root)
    base = Path(validated["root"])
    backup_base_input = Path(backup_root).expanduser() if backup_root else (
        Path.home() / ".botpyton" / "backups" / hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:16]
    )
    _reject_link_components(backup_base_input, "Backup directory")
    backup_base = backup_base_input.resolve()
    try:
        backup_base.relative_to(base)
        backup_is_inside_project = True
    except ValueError:
        backup_is_inside_project = False
    if backup_is_inside_project:
        raise LocalChangeError("Backup directory must be outside the project root")
    plan_fingerprint = _sha256(json.dumps(
        [{"path": item["path"], "new_sha256": item["new_sha256"],
          "expected_sha256": item["expected_sha256"]} for item in validated["changes"]],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))

    backup_base.mkdir(parents=True, exist_ok=True)
    _reject_link_components(backup_base, "Backup directory")
    journal_path = _transaction_journal_path(backup_base, validated["task_id"], plan_fingerprint)
    with exclusive_file_lock(journal_path.parent / f".{plan_fingerprint}.transaction.lock"):
        transaction_entries = []
        for item in validated["changes"]:
            transaction_entries.append({
                "path": item["path"],
                "original_exists": item["exists"],
                "original_sha256": item["current_sha256"],
                "new_sha256": item["new_sha256"],
                "backup_relative_path": (
                    PurePosixPath(validated["task_id"], plan_fingerprint, item["path"]).as_posix()
                    if item["exists"] else None
                ),
                "status": "pending",
                "published_identity": None,
            })
        journal = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "transaction_id": plan_fingerprint,
            "task_id": validated["task_id"],
            "project_root": str(base),
            "backup_root": str(backup_base),
            "created_at": _transaction_timestamp(),
            "updated_at": _transaction_timestamp(),
            "status": "prepared",
            "changes": transaction_entries,
        }
        try:
            _persist_transaction_journal(journal_path, journal, create=True)
        except LocalChangeError:
            raise
        except Exception as exc:
            raise LocalChangeError(
                f"Could not create transaction journal before project writes: {type(exc).__name__}"
            ) from exc

        written: list[Path] = []
        written_hashes: dict[Path, str] = {}
        backups: dict[Path, bytes | None] = {}
        backup_records = []
        entries_by_path = {item["path"]: item for item in journal["changes"]}
        try:
            for item in validated["changes"]:
                _reject_link_components(base, "Project root")
                target = _resolve_target(base, item["path"])
                _reject_link_components(target.parent, "Project file parent")
                original = target.read_bytes() if item["exists"] else None
                if original is not None and _sha256(original) != item["expected_sha256"]:
                    raise LocalChangeError(f"File changed immediately before write: {item['path']}")
                if original is None and target.exists():
                    raise LocalChangeError(f"File appeared immediately before write: {item['path']}")
                backups[target] = original
                journal_entry = entries_by_path[item["path"]]
                if original is not None:
                    backup_path = backup_base / validated["task_id"] / plan_fingerprint / item["path"]
                    expected_backup_sha256 = _sha256(original)
                    _reject_link_components(backup_path.parent, "Backup file")
                    if backup_path.exists() or backup_path.is_symlink():
                        _reject_link_components(backup_path, "Backup file")
                        if not backup_path.is_file() or _sha256(backup_path.read_bytes()) != expected_backup_sha256:
                            raise LocalChangeError("An existing backup does not match the expected original file; it was preserved.")
                    else:
                        _write_atomic(backup_path, original, replace_existing=False)
                    backup_records.append({
                        "path": item["path"],
                        "backup_path": str(backup_path),
                        "sha256": _sha256(original),
                    })
                    journal_entry["status"] = "backed_up"
                    _persist_transaction_journal(journal_path, journal)

                journal_entry["status"] = "publishing"
                journal["status"] = "applying"
                _persist_transaction_journal(journal_path, journal)
                try:
                    _write_atomic(
                        target,
                        item["content"].encode("utf-8"),
                        replace_existing=bool(item["exists"]),
                        expected_current_sha256=item["expected_sha256"],
                    )
                except _PublishedCleanupError as publish_error:
                    # The destination was published before temporary-alias cleanup failed.
                    # Track it before propagating so the normal guarded rollback can run.
                    if (
                        publish_error.path == target
                        and target.is_file()
                        and _sha256(target.read_bytes()) == item["new_sha256"]
                    ):
                        written.append(target)
                        written_hashes[target] = item["new_sha256"]
                        journal_entry["status"] = "published"
                        journal_entry["published_identity"] = _transaction_identity(target)
                        try:
                            _persist_transaction_journal(journal_path, journal)
                        except Exception:
                            pass
                        try:
                            publish_error.temporary_path.unlink(missing_ok=True)
                        except OSError:
                            # Preserve accurate error disclosure if this alias cannot be cleaned.
                            pass
                    raise

                written.append(target)
                written_hashes[target] = item["new_sha256"]
                journal_entry["status"] = "published"
                journal_entry["published_identity"] = _transaction_identity(target)
                _persist_transaction_journal(journal_path, journal)

            journal["status"] = "completed"
            _persist_transaction_journal(journal_path, journal)
        except Exception as exc:
            rollback_errors: list[str] = []
            if isinstance(exc, _PublishedCleanupError):
                try:
                    exc.temporary_path.unlink(missing_ok=True)
                except OSError:
                    rollback_errors.append("staging_alias_cleanup_failed")
            for target in reversed(written):
                relative = target.relative_to(base).as_posix()
                journal_entry = entries_by_path[relative]
                try:
                    _reject_link_components(target.parent, "Rollback target")
                    _reject_link_components(target, "Rollback target")
                    if not target.is_file() or _is_link_or_reparse(target):
                        raise OSError("rollback skipped because target is no longer a regular file")
                    current = read_bounded_bytes(
                        target, limit_bytes=MAX_FILE_BYTES, label="Local-change rollback target"
                    )
                    if _sha256(current) != written_hashes[target]:
                        raise OSError("rollback skipped because target changed after BOTPYTON wrote it")
                    expected_identity = journal_entry["published_identity"]
                    if expected_identity is not None and _transaction_identity(target) != expected_identity:
                        raise OSError("rollback skipped because target identity changed after BOTPYTON wrote it")
                    original = backups[target]
                    if original is None:
                        target.unlink()
                    else:
                        _write_atomic(
                            target, original, expected_current_sha256=written_hashes[target]
                        )
                    journal_entry["status"] = "rolled_back"
                    journal_entry["published_identity"] = None
                    _persist_transaction_journal(journal_path, journal)
                except Exception as rollback_exc:
                    if isinstance(rollback_exc, _PublishedCleanupError):
                        try:
                            rollback_exc.temporary_path.unlink(missing_ok=True)
                        except OSError:
                            rollback_errors.append(f"{target}: staging_alias_cleanup_failed")
                    try:
                        current_after_error = (
                            read_bounded_bytes(
                                target, limit_bytes=MAX_FILE_BYTES, label="Rollback conflict probe"
                            ) if target.is_file() and not _is_link_or_reparse(target) else None
                        )
                        original_after_error = backups[target]
                        if (
                            (original_after_error is not None and current_after_error == original_after_error)
                            or (original_after_error is None and current_after_error is None)
                        ):
                            # The file rollback committed even if a subsequent journal
                            # write failed; retry recording that verified state.
                            journal_entry["status"] = "rolled_back"
                            journal_entry["published_identity"] = None
                        elif current_after_error is None or _sha256(current_after_error) != written_hashes[target]:
                            journal_entry["status"] = "external_change_preserved"
                            rollback_errors.append(f"{target}: external_change_preserved")
                        else:
                            journal_entry["status"] = "rollback_failed"
                            rollback_errors.append(f"{target}: {type(rollback_exc).__name__}")
                        _persist_transaction_journal(journal_path, journal)
                    except Exception:
                        journal_entry["status"] = "rollback_failed"
                        rollback_errors.append(f"{target}: {type(rollback_exc).__name__}")

            # Record changes which were never positively confirmed as published.
            # An uncertain publication is never deleted or overwritten automatically.
            for item in journal["changes"]:
                if item["path"] in {path.relative_to(base).as_posix() for path in written}:
                    continue
                if item["status"] in {"pending", "backed_up", "publishing"}:
                    try:
                        target = _resolve_target(base, item["path"])
                        if item["original_exists"]:
                            if target.is_file() and _sha256(read_bounded_bytes(
                                target, limit_bytes=MAX_FILE_BYTES, label="Unpublished transaction target"
                            )) == item["original_sha256"]:
                                item["status"] = "rolled_back"
                            else:
                                item["status"] = "ambiguous"
                        else:
                            item["status"] = "rolled_back" if not target.exists() else "ambiguous"
                        _persist_transaction_journal(journal_path, journal)
                    except Exception:
                        item["status"] = "ambiguous"
                        rollback_errors.append(f"{item['path']}: recovery_state_unverified")
                        try:
                            _persist_transaction_journal(journal_path, journal)
                        except Exception:
                            pass

            journal["status"] = "rollback_partial" if rollback_errors or any(
                item["status"] not in {"rolled_back", "already_original"}
                for item in journal["changes"]
            ) else "rolled_back"
            try:
                _persist_transaction_journal(journal_path, journal)
            except Exception as journal_exc:
                rollback_errors.append(f"transaction journal: {type(journal_exc).__name__}")
            message = f"Local change failed and rollback was attempted: {type(exc).__name__}: {exc}"
            if rollback_errors:
                message += "; unresolved recovery: " + ", ".join(rollback_errors)
            message += f"; transaction journal: {journal_path}"
            raise LocalChangeError(message) from exc

        return {
            **_public_summary(validated),
            "mode": "executed",
            "writes_performed": True,
            "backups": backup_records,
            "plan_fingerprint": plan_fingerprint,
            "transaction_id": plan_fingerprint,
            "transaction_journal_path": str(journal_path),
            "written_files": [item["path"] for item in validated["changes"]],
            "rollback_performed": False,
        }


def read_local_plan(path: str | Path, *, max_bytes: int = MAX_PLAN_BYTES) -> Any:
    plan_path = Path(path).expanduser().resolve()
    try:
        raw = read_bounded_bytes(plan_path, limit_bytes=max_bytes, label="Local change plan")
        value = json.loads(raw.decode("utf-8"))
        enforce_json_depth(value, label="Local change plan")
        return value
    except (BoundedReadError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LocalChangeError(f"Plan is not safe UTF-8 JSON: {exc}") from exc


def inspect_local_change_transaction(
    journal_path: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Read-only inspection of a durable local change transaction journal."""
    from .local_change_journal import inspect_local_change_transaction as inspect
    return inspect(journal_path, project_root)


def rollback_local_change_transaction(
    journal_path: str | Path,
    project_root: str | Path,
    *,
    expected_transaction_id: str,
) -> dict[str, Any]:
    """Explicitly roll back only published files whose identity and hash still match."""
    from .local_change_journal import rollback_local_change_transaction as rollback
    return rollback(
        journal_path, project_root, expected_transaction_id=expected_transaction_id
    )
