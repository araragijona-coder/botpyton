"""Durable transaction journal, read-only inspection, and guarded rollback."""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .file_lock import exclusive_file_lock
from . import local_changes as _local_changes

LocalChangeError = _local_changes.LocalChangeError
MAX_TRANSACTION_BYTES = _local_changes.MAX_TRANSACTION_BYTES
TRANSACTION_SCHEMA_VERSION = _local_changes.TRANSACTION_SCHEMA_VERSION
def _journal_checksum(journal: dict[str, Any]) -> str:
    """Detect accidental journal corruption; not an authentication mechanism."""
    payload = {key: value for key, value in journal.items() if key != "journal_sha256"}
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise LocalChangeError("Transaction journal cannot be canonicalized safely") from exc
    return _local_changes._sha256(canonical)


def _transaction_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _transaction_identity(path: Path) -> dict[str, int] | None:
    """Return a stable-looking regular-file identity where the platform exposes one."""
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or _local_changes._is_link_or_reparse(path) or not getattr(info, "st_ino", 0):
        return None
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "mtime_ns": int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
    }


def _duplicate_transaction_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalChangeError("Transaction journal contains duplicate JSON keys")
        result[key] = value
    return result


def _transaction_journal_path(backup_root: Path, task_id: str, transaction_id: str) -> Path:
    return backup_root / ".botpyton-transactions" / f"{task_id}-{transaction_id}.json"


def _persist_transaction_journal(
    journal_path: Path,
    journal: dict[str, Any],
    *,
    create: bool = False,
) -> None:
    journal["updated_at"] = _transaction_timestamp()
    journal.pop("journal_sha256", None)
    journal["journal_sha256"] = _journal_checksum(journal)
    try:
        raw = (json.dumps(
            journal, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise LocalChangeError("Transaction journal could not be serialized safely") from exc
    if len(raw) > MAX_TRANSACTION_BYTES:
        raise LocalChangeError("Transaction journal exceeds the configured size limit")
    _local_changes._reject_link_components(journal_path.parent, "Transaction journal directory")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    _local_changes._reject_link_components(journal_path.parent, "Transaction journal directory")
    _local_changes._reject_link_components(journal_path, "Transaction journal")
    try:
        if create:
            _local_changes._write_atomic(journal_path, raw, replace_existing=False)
        else:
            previous = read_bounded_bytes(
                journal_path, limit_bytes=MAX_TRANSACTION_BYTES,
                label="Existing local-change transaction journal",
            )
            _local_changes._write_atomic(
                journal_path, raw, replace_existing=True,
                expected_current_sha256=_local_changes._sha256(previous),
            )
    except _local_changes._PublishedCleanupError as exc:
        try:
            exc.temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            raise LocalChangeError(
                "Transaction journal was published but its staging alias could not be cleaned"
            ) from cleanup_exc
        try:
            published_journal = read_bounded_bytes(
                journal_path, limit_bytes=MAX_TRANSACTION_BYTES,
                label="Published local-change transaction journal",
            )
        except (BoundedReadError, OSError) as verify_exc:
            raise LocalChangeError("Transaction journal publication could not be verified") from verify_exc
        if _local_changes._sha256(published_journal) != _local_changes._sha256(raw):
            raise LocalChangeError("Transaction journal changed at its publication boundary")
    except FileExistsError as exc:
        raise LocalChangeError(
            "A transaction journal already exists for this task and plan; inspect it before retrying"
        ) from exc
    except (OSError, BoundedReadError) as exc:
        raise LocalChangeError(
            f"Could not persist transaction journal safely: {type(exc).__name__}"
        ) from exc


def _load_transaction_journal(journal_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(journal_path).expanduser()
    _local_changes._reject_link_components(path, "Transaction journal")
    try:
        if not path.is_file():
            raise LocalChangeError("Transaction journal must be an existing regular file")
        raw = read_bounded_bytes(
            path, limit_bytes=MAX_TRANSACTION_BYTES, label="Local-change transaction journal"
        )
        journal = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_duplicate_transaction_key
        )
        enforce_json_depth(journal, label="Local-change transaction journal")
    except LocalChangeError:
        raise
    except (BoundedReadError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LocalChangeError(
            f"Transaction journal is invalid or unsafe: {type(exc).__name__}"
        ) from exc

    required = {
        "schema_version", "transaction_id", "task_id", "project_root", "backup_root",
        "created_at", "updated_at", "status", "changes", "journal_sha256",
    }
    if not isinstance(journal, dict) or set(journal) != required:
        raise LocalChangeError("Transaction journal fields do not match the supported schema")
    if (
        not isinstance(journal["schema_version"], int)
        or isinstance(journal["schema_version"], bool)
        or journal["schema_version"] != TRANSACTION_SCHEMA_VERSION
    ):
        raise LocalChangeError("Unsupported transaction journal schema version")
    journal_checksum = journal["journal_sha256"]
    if not isinstance(journal_checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", journal_checksum):
        raise LocalChangeError("Transaction journal integrity checksum is malformed")
    if _journal_checksum(journal) != journal_checksum:
        raise LocalChangeError("Transaction journal integrity checksum mismatch; recovery is blocked")
    transaction_id = journal["transaction_id"]
    task_id = journal["task_id"]
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{64}", transaction_id):
        raise LocalChangeError("Transaction journal ID is malformed")
    if not isinstance(task_id, str) or not _local_changes.TASK_ID_RE.fullmatch(task_id):
        raise LocalChangeError("Transaction journal task ID is malformed")
    allowed_statuses = {
        "prepared", "applying", "completed", "rolled_back", "rollback_partial",
    }
    if not isinstance(journal["status"], str) or journal["status"] not in allowed_statuses:
        raise LocalChangeError("Transaction journal state is unsupported")
    if not all(isinstance(journal[key], str) and journal[key] for key in ("created_at", "updated_at")):
        raise LocalChangeError("Transaction journal timestamps are invalid")
    if not isinstance(journal["project_root"], str) or not isinstance(journal["backup_root"], str):
        raise LocalChangeError("Transaction journal roots must be absolute path strings")

    project_root_input = Path(journal["project_root"]).expanduser()
    backup_root_input = Path(journal["backup_root"]).expanduser()
    if not project_root_input.is_absolute() or not backup_root_input.is_absolute():
        raise LocalChangeError("Transaction journal roots must be absolute")
    _local_changes._reject_link_components(project_root_input, "Transaction project root")
    _local_changes._reject_link_components(backup_root_input, "Transaction backup root")
    try:
        project_root = project_root_input.resolve(strict=True)
        backup_root = backup_root_input.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalChangeError("Transaction roots could not be resolved safely") from exc
    if not project_root.is_dir() or not backup_root.is_dir():
        raise LocalChangeError("Transaction project and backup roots must be existing directories")
    try:
        backup_root.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise LocalChangeError("Transaction backup root must be outside the project root")

    expected_journal = _transaction_journal_path(backup_root, task_id, transaction_id)
    try:
        if path.resolve(strict=True) != expected_journal.resolve(strict=True):
            raise LocalChangeError("Transaction journal location does not match its declared roots and ID")
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalChangeError("Transaction journal location could not be validated") from exc

    changes = journal["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= _local_changes.MAX_FILES:
        raise LocalChangeError("Transaction journal changes must contain a bounded non-empty list")
    seen: set[str] = set()
    fingerprint_items = []
    allowed_change_statuses = {
        "pending", "backed_up", "publishing", "published", "rolled_back",
        "already_original", "external_change_preserved", "rollback_failed", "ambiguous",
    }
    for item in changes:
        if not isinstance(item, dict) or set(item) != {
            "path", "original_exists", "original_sha256", "new_sha256",
            "backup_relative_path", "status", "published_identity",
        }:
            raise LocalChangeError("Transaction change entry fields are invalid")
        relative = _local_changes._safe_relative_path(item["path"])
        if relative in seen:
            raise LocalChangeError("Transaction journal contains duplicate target paths")
        seen.add(relative)
        if not isinstance(item["original_exists"], bool):
            raise LocalChangeError("Transaction journal original_exists must be boolean")
        original_sha = item["original_sha256"]
        new_sha = item["new_sha256"]
        if not isinstance(new_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", new_sha):
            raise LocalChangeError("Transaction journal new SHA-256 is malformed")
        if item["original_exists"]:
            if not isinstance(original_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", original_sha):
                raise LocalChangeError("Transaction journal original SHA-256 is malformed")
            expected_backup = PurePosixPath(task_id, transaction_id, relative).as_posix()
            if item["backup_relative_path"] != expected_backup:
                raise LocalChangeError("Transaction journal backup path is not canonical")
        else:
            if original_sha is not None or item["backup_relative_path"] is not None:
                raise LocalChangeError("New-file transaction entries cannot declare an original backup")
        if not isinstance(item["status"], str) or item["status"] not in allowed_change_statuses:
            raise LocalChangeError("Transaction journal change state is unsupported")
        identity = item["published_identity"]
        if identity is not None:
            if (
                not isinstance(identity, dict)
                or set(identity) != {"device", "inode", "size", "mtime_ns"}
                or any(not isinstance(identity[key], int) or isinstance(identity[key], bool)
                       or identity[key] < 0 for key in identity)
            ):
                raise LocalChangeError("Transaction journal file identity is invalid")
        fingerprint_items.append({
            "path": relative, "new_sha256": new_sha,
            "expected_sha256": original_sha if item["original_exists"] else None,
        })
    if [item["path"] for item in changes] != sorted(seen):
        raise LocalChangeError("Transaction journal changes are not in canonical sorted order")
    fingerprint = _local_changes._sha256(json.dumps(
        fingerprint_items, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    if fingerprint != transaction_id:
        raise LocalChangeError("Transaction journal ID does not match the declared change hashes")

    journal["project_root"] = str(project_root)
    journal["backup_root"] = str(backup_root)
    return path.resolve(strict=True), journal


def _inspect_transaction_targets(journal_path: Path, journal: dict[str, Any]) -> dict[str, Any]:
    root = Path(journal["project_root"])
    backup_root = Path(journal["backup_root"])
    results = []
    for item in journal["changes"]:
        relative = item["path"]
        current_sha: str | None = None
        current_identity: dict[str, int] | None = None
        try:
            target = _local_changes._resolve_target(root, relative)
            if target.exists():
                _local_changes._reject_link_components(target, "Transaction target")
                if not target.is_file() or target.stat().st_size > _local_changes.MAX_FILE_BYTES:
                    target_state = "unsafe_or_nonregular"
                else:
                    current_sha = _local_changes._sha256(read_bounded_bytes(
                        target, limit_bytes=_local_changes.MAX_FILE_BYTES, label="Transaction target"
                    ))
                    current_identity = _transaction_identity(target)
                    if item["original_exists"] and current_sha == item["original_sha256"]:
                        target_state = "original_content"
                    elif current_sha == item["new_sha256"]:
                        target_state = "new_content"
                    else:
                        target_state = "changed_content"
            else:
                target_state = "original_absent" if not item["original_exists"] else "target_missing"
        except (LocalChangeError, OSError, BoundedReadError):
            target_state = "unsafe_or_unreadable"

        backup_valid = None
        if item["original_exists"]:
            try:
                backup_path = _local_changes._resolve_target(backup_root, item["backup_relative_path"])
                _local_changes._reject_link_components(backup_path, "Transaction backup")
                backup_valid = (
                    backup_path.is_file()
                    and backup_path.stat().st_size <= _local_changes.MAX_FILE_BYTES
                    and _local_changes._sha256(read_bounded_bytes(
                        backup_path, limit_bytes=_local_changes.MAX_FILE_BYTES, label="Transaction backup"
                    )) == item["original_sha256"]
                )
            except (LocalChangeError, OSError, BoundedReadError):
                backup_valid = False

        already_original = (
            target_state == "original_content" if item["original_exists"]
            else target_state == "original_absent"
        )
        identity_matches = (
            item["published_identity"] is not None
            and current_identity == item["published_identity"]
        )
        rollback_safe = (
            item["status"] == "published"
            and target_state == "new_content"
            and identity_matches
            and (not item["original_exists"] or backup_valid is True)
        )
        if already_original:
            recommended_action = "already_restored"
        elif rollback_safe:
            recommended_action = "rollback_safe"
        elif target_state == "new_content" and item["status"] == "publishing":
            recommended_action = "manual_review_ambiguous_publication"
        else:
            recommended_action = "manual_review_preserve_current_state"

        results.append({
            "path": relative,
            "journal_state": item["status"],
            "target_state": target_state,
            "current_sha256": current_sha,
            "backup_valid": backup_valid,
            "published_identity_matches": identity_matches,
            "recommended_action": recommended_action,
        })
    return {
        "status": "inspection_complete",
        "transaction_id": journal["transaction_id"],
        "transaction_status": journal["status"],
        "journal_path": str(journal_path),
        "project_root": str(root),
        "changes": results,
        "writes_performed": False,
    }


def inspect_local_change_transaction(
    journal_path: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Read-only, transaction-consistent inspection without creating lock files."""
    resolved_journal, journal = _load_transaction_journal(journal_path)
    lock_path = resolved_journal.parent / f".{journal['transaction_id']}.transaction.lock"
    with exclusive_file_lock(lock_path, create=False):
        # Reload only after the shared transaction lock is held so the journal
        # and target observations describe a consistent point in the transaction.
        resolved_journal, journal = _load_transaction_journal(resolved_journal)
        requested_root = Path(project_root).expanduser()
        _local_changes._reject_link_components(requested_root, "Selected project root")
        try:
            requested_root = requested_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise LocalChangeError("Selected project root cannot be resolved safely") from exc
        if requested_root != Path(journal["project_root"]):
            raise LocalChangeError("Selected project root does not match the transaction journal")
        return _inspect_transaction_targets(resolved_journal, journal)


def rollback_local_change_transaction(
    journal_path: str | Path,
    project_root: str | Path,
    *,
    expected_transaction_id: str,
) -> dict[str, Any]:
    """Explicitly roll back only published files whose identity and hash still match."""
    resolved_journal, journal = _load_transaction_journal(journal_path)
    if not isinstance(expected_transaction_id, str) or expected_transaction_id != journal["transaction_id"]:
        raise LocalChangeError("Transaction ID confirmation does not match; no recovery writes were performed")
    requested_root = Path(project_root).expanduser()
    _local_changes._reject_link_components(requested_root, "Selected project root")
    try:
        requested_root = requested_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalChangeError("Selected project root cannot be resolved safely") from exc
    if requested_root != Path(journal["project_root"]):
        raise LocalChangeError("Selected project root does not match the transaction journal")
    if journal["status"] == "rolled_back":
        result = _inspect_transaction_targets(resolved_journal, journal)
        result["recovery"] = "already_rolled_back"
        result["writes_performed"] = False
        return result

    lock_path = resolved_journal.parent / f".{journal['transaction_id']}.transaction.lock"
    _local_changes._reject_link_components(lock_path, "Transaction recovery lock")
    with exclusive_file_lock(lock_path):
        resolved_journal, journal = _load_transaction_journal(resolved_journal)
        if expected_transaction_id != journal["transaction_id"] or requested_root != Path(journal["project_root"]):
            raise LocalChangeError("Transaction changed while acquiring its recovery lock")
        journal["status"] = "applying"
        _persist_transaction_journal(resolved_journal, journal)
        root = Path(journal["project_root"])
        backup_root = Path(journal["backup_root"])
        recovery_writes_performed = False
        for item in reversed(journal["changes"]):
            try:
                target = _local_changes._resolve_target(root, item["path"])
                if target.exists():
                    _local_changes._reject_link_components(target, "Recovery target")
                    if not target.is_file() or target.stat().st_size > _local_changes.MAX_FILE_BYTES:
                        item["status"] = "external_change_preserved"
                        _persist_transaction_journal(resolved_journal, journal)
                        continue
                    current = read_bounded_bytes(
                        target, limit_bytes=_local_changes.MAX_FILE_BYTES, label="Recovery target"
                    )
                    current_sha = _local_changes._sha256(current)
                else:
                    current = None
                    current_sha = None

                is_original = (
                    current_sha == item["original_sha256"]
                    if item["original_exists"] else current is None
                )
                if is_original:
                    item["status"] = "rolled_back"
                    _persist_transaction_journal(resolved_journal, journal)
                    continue

                current_identity = _transaction_identity(target) if current is not None else None
                if not (
                    item["status"] == "published"
                    and current_sha == item["new_sha256"]
                    and item["published_identity"] is not None
                    and current_identity == item["published_identity"]
                ):
                    item["status"] = (
                        "ambiguous" if item["status"] == "publishing" and current_sha == item["new_sha256"]
                        else "external_change_preserved"
                    )
                    _persist_transaction_journal(resolved_journal, journal)
                    continue

                if item["original_exists"]:
                    backup_path = _local_changes._resolve_target(backup_root, item["backup_relative_path"])
                    _local_changes._reject_link_components(backup_path, "Recovery backup")
                    if not backup_path.is_file() or backup_path.stat().st_size > _local_changes.MAX_FILE_BYTES:
                        raise LocalChangeError("Verified transaction backup is missing or not a regular bounded file")
                    original = read_bounded_bytes(
                        backup_path, limit_bytes=_local_changes.MAX_FILE_BYTES, label="Recovery backup"
                    )
                    if _local_changes._sha256(original) != item["original_sha256"]:
                        raise LocalChangeError("Transaction backup hash mismatch; current destination preserved")
                    _local_changes._write_atomic(
                        target, original, expected_current_sha256=item["new_sha256"]
                    )
                    recovery_writes_performed = True
                else:
                    _local_changes._reject_link_components(target, "Recovery target")
                    if (
                        not target.is_file()
                        or _transaction_identity(target) != item["published_identity"]
                        or _local_changes._sha256(read_bounded_bytes(
                            target, limit_bytes=_local_changes.MAX_FILE_BYTES, label="New recovery target"
                        )) != item["new_sha256"]
                    ):
                        item["status"] = "external_change_preserved"
                        _persist_transaction_journal(resolved_journal, journal)
                        continue
                    target.unlink()
                    recovery_writes_performed = True

                item["status"] = "rolled_back"
                item["published_identity"] = None
                _persist_transaction_journal(resolved_journal, journal)
            except Exception as recovery_exc:
                # A post-publication cleanup error may occur after restoration has
                # already committed. Retry that alias cleanup and trust the target
                # only when its restored bytes can be independently verified.
                if isinstance(recovery_exc, _PublishedCleanupError):
                    try:
                        recovery_exc.temporary_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    if item["original_exists"]:
                        restored = target.is_file() and not _local_changes._is_link_or_reparse(target) and (
                            _local_changes._sha256(read_bounded_bytes(
                                target, limit_bytes=_local_changes.MAX_FILE_BYTES, label="Recovery post-error probe"
                            )) == item["original_sha256"]
                        )
                    else:
                        restored = not target.exists()
                except Exception:
                    restored = False
                if restored:
                    item["status"] = "rolled_back"
                    item["published_identity"] = None
                    recovery_writes_performed = True
                elif item["status"] not in {"external_change_preserved", "ambiguous"}:
                    item["status"] = "rollback_failed"
                try:
                    _persist_transaction_journal(resolved_journal, journal)
                except Exception:
                    pass

        complete = all(
            item["status"] in {"rolled_back", "already_original"}
            for item in journal["changes"]
        )
        journal["status"] = "rolled_back" if complete else "rollback_partial"
        _persist_transaction_journal(resolved_journal, journal)

    result = _inspect_transaction_targets(resolved_journal, journal)
    result["recovery"] = "rollback_complete" if journal["status"] == "rolled_back" else "rollback_partial"
    result["writes_performed"] = recovery_writes_performed
    return result


