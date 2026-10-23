"""On-demand, one-order inbox/outbox for structured architect packets.

No watcher, daemon, network client, shell dispatch, or permission inference is added.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable

from .application_cache import _is_link_or_reparse
from .architect_order_contract import ArchitectOrderError, validate_architect_order
from .architect_order_response import (
    ArchitectResponseError,
    ArchitectResponseWriter,
    build_architect_response,
    build_rejected_architect_response,
)
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes

MAX_SPOOL_DIRECTORY_ENTRIES = 5_000
MAX_SPOOL_ORDERS = 500
MAX_ORDER_BYTES = 600_000
ORDER_READ_ERROR_STATUSES = frozenset({"rejected_invalid_order", "authorization_required", "authorization_mismatch"})


class ArchitectSpoolError(ValueError):
    """Raised when inbox/outbox cannot be accessed within the spool safety policy."""


def _checked_directory(path_text: str | Path, label: str) -> Path:
    requested = Path(path_text).expanduser()
    if _is_link_or_reparse(requested):
        raise ArchitectSpoolError(f"{label} must be an existing real directory, not a link/reparse point")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ArchitectSpoolError(f"{label} directory must already exist: {exc}") from exc
    if not resolved.is_dir() or _is_link_or_reparse(resolved):
        raise ArchitectSpoolError(f"{label} must resolve to an existing real directory")
    return resolved


def _list_candidates(inbox: Path) -> tuple[list[Path], list[dict[str, str]]]:
    candidates: list[Path] = []
    skipped: list[dict[str, str]] = []
    try:
        with os.scandir(inbox) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > MAX_SPOOL_DIRECTORY_ENTRIES:
                    raise ArchitectSpoolError(
                        f"Inbox has more than {MAX_SPOOL_DIRECTORY_ENTRIES} entries; refusing an unbounded scan"
                    )
                if not entry.name.lower().endswith(".json"):
                    continue
                path = inbox / entry.name
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    skipped.append({"file": entry.name, "reason": "cannot_stat_input"})
                    continue
                if not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(path):
                    skipped.append({"file": entry.name, "reason": "not_a_regular_file_or_is_link"})
                    continue
                candidates.append(path)
    except ArchitectSpoolError:
        raise
    except OSError as exc:
        raise ArchitectSpoolError(f"Cannot enumerate inbox: {exc}") from exc
    if len(candidates) > MAX_SPOOL_ORDERS:
        raise ArchitectSpoolError(
            f"Inbox has {len(candidates)} JSON files; limit is {MAX_SPOOL_ORDERS} per bounded scan"
        )
    candidates.sort(key=lambda path: path.name.casefold())
    return candidates, skipped


def _profile(*, execute: bool, authorize_local_writes: bool, execute_project_code: bool) -> str:
    if not execute:
        return "review"
    return f"execute-w{int(authorize_local_writes)}-c{int(execute_project_code)}"


def _response_path(outbox: Path, request_sha256: str, profile: str) -> Path:
    return outbox / f"order-{request_sha256}.{profile}.response.json"


def _read_existing_response(path: Path, expected_sha256: str) -> dict[str, Any]:
    if _is_link_or_reparse(path):
        raise ArchitectSpoolError(f"Existing outbox response is a link/reparse point: {path.name}")
    try:
        raw = read_bounded_bytes(
            path, limit_bytes=4 * 1024 * 1024, label="existing architect response"
        )
        value = json.loads(raw.decode("utf-8"))
        enforce_json_depth(value, label="existing architect response")
    except (BoundedReadError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ArchitectSpoolError(
            f"Existing response for this order is not a valid bounded JSON record: {path.name}: {exc}"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("response_type") != "botpyton.architect_response"
        or value.get("schema_version") != 1
        or value.get("request_sha256") != expected_sha256
    ):
        raise ArchitectSpoolError(
            f"Existing response does not match the expected order hash/schema: {path.name}"
        )
    return value


def _write_response(path: Path, response: dict[str, Any]) -> None:
    response["response_file"] = path.name
    with ArchitectResponseWriter(path) as writer:
        writer.write(response)


def run_order_spool(
    *,
    inbox_path: str | Path,
    outbox_path: str | Path,
    execute: bool,
    authorize_local_writes: bool,
    execute_project_code: bool,
    project_root: str,
    memory_path: str,
    check_timeout: int,
    run_order: Callable[[Path, Path, dict[str, bool]], dict[str, Any]],
) -> dict[str, Any]:
    """Process at most one pending JSON input and durably return its correlated response.

    The callback invokes the normal strict CLI path on a private exact-byte snapshot.
    A prior valid response for the same packet hash and authorization profile is
    immutable and causes that input to be skipped, preventing accidental replay.
    """
    inbox = _checked_directory(inbox_path, "Architect inbox")
    outbox = _checked_directory(outbox_path, "Architect outbox")
    if inbox == outbox or inbox.is_relative_to(outbox) or outbox.is_relative_to(inbox):
        raise ArchitectSpoolError("Inbox and outbox must be distinct directories and must not contain one another")

    if not execute and (authorize_local_writes or execute_project_code):
        raise ArchitectSpoolError(
            "Local-write/project-code permissions require --execute-architect-order"
        )
    profile = _profile(
        execute=execute,
        authorize_local_writes=authorize_local_writes,
        execute_project_code=execute_project_code,
    )
    flags = {
        "execute_architect_order": execute,
        "authorize_local_writes": authorize_local_writes,
        "execute_project_code": execute_project_code,
    }
    candidates, skipped = _list_candidates(inbox)
    already_responded: list[str] = []
    unreadable: list[dict[str, str]] = []

    for candidate in candidates:
        if _is_link_or_reparse(candidate):
            skipped.append({"file": candidate.name, "reason": "link_or_reparse_point"})
            continue
        try:
            raw = read_bounded_bytes(
                candidate, limit_bytes=MAX_ORDER_BYTES, label="architect inbox packet"
            )
        except BoundedReadError as exc:
            unreadable.append({"file": candidate.name, "reason": str(exc)[:500]})
            continue
        request_sha256 = hashlib.sha256(raw).hexdigest()
        response_path = _response_path(outbox, request_sha256, profile)
        try:
            response_path.lstat()
            response_exists = True
        except FileNotFoundError:
            response_exists = False
        except OSError as exc:
            raise ArchitectSpoolError(f"Cannot inspect outbox target {response_path.name}: {exc}") from exc
        if response_exists:
            _read_existing_response(response_path, request_sha256)
            already_responded.append(candidate.name)
            continue

        try:
            packet = json.loads(raw.decode("utf-8"))
            enforce_json_depth(packet, label="architect inbox packet")
            order = validate_architect_order(packet)
        except (UnicodeError, json.JSONDecodeError, RecursionError, BoundedReadError, ArchitectOrderError) as exc:
            response = build_rejected_architect_response(
                request_sha256, candidate.name, f"{type(exc).__name__}: {exc}", flags
            )
            _write_response(response_path, response)
            return {
                "spool_status": "processed",
                "status": response["status"],
                "input_file": candidate.name,
                "request_sha256": request_sha256,
                "response_file": response_path.name,
                "exit_code": 2,
                "response": response,
                "already_responded_skipped": already_responded,
                "skipped_inputs": skipped,
                "unreadable_inputs": unreadable,
            }

        required = set(order["required_local_approvals"])
        if order["status"] != "needs_input":
            missing = required.difference(
                capability for capability, enabled in (
                    ("local_file_write", authorize_local_writes),
                    ("project_code_execution", execute_project_code),
                ) if enabled
            )
            extra = set()
            if authorize_local_writes and "local_file_write" not in required:
                extra.add("--authorize-local-writes")
            if execute_project_code and "project_code_execution" not in required:
                extra.add("--execute-project-code")
            if execute and missing:
                response = build_architect_response(
                    order, request_sha256, status="authorization_required",
                    execution_started=False, queue_mutated=False,
                    operator_flags=flags,
                )
                response["missing_local_approvals"] = sorted(missing)
                response["error"] = (
                    "The order was not started. Review it, then explicitly add the required local permission flags."
                )
                response["source_file"] = candidate.name
                _write_response(response_path, response)
                return {
                    "spool_status": "processed",
                    "status": response["status"],
                    "input_file": candidate.name,
                    "request_sha256": request_sha256,
                    "response_file": response_path.name,
                    "exit_code": 2,
                    "response": response,
                    "already_responded_skipped": already_responded,
                    "skipped_inputs": skipped,
                    "unreadable_inputs": unreadable,
                }
            if execute and extra:
                response = build_architect_response(
                    order, request_sha256, status="authorization_mismatch",
                    execution_started=False, queue_mutated=False,
                    operator_flags=flags,
                )
                response["unexpected_local_approvals"] = sorted(extra)
                response["error"] = "An approval was supplied for a capability not requested by this packet."
                response["source_file"] = candidate.name
                _write_response(response_path, response)
                return {
                    "spool_status": "processed",
                    "status": response["status"],
                    "input_file": candidate.name,
                    "request_sha256": request_sha256,
                    "response_file": response_path.name,
                    "exit_code": 2,
                    "response": response,
                    "already_responded_skipped": already_responded,
                    "skipped_inputs": skipped,
                    "unreadable_inputs": unreadable,
                }

        descriptor = None
        snapshot: Path | None = None
        try:
            descriptor, snapshot_name = tempfile.mkstemp(
                prefix=".botpyton-order-", suffix=".json", dir=outbox
            )
            snapshot = Path(snapshot_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            child = run_order(snapshot, response_path, flags)
            try:
                response = _read_existing_response(response_path, request_sha256)
            except ArchitectSpoolError:
                # If the strict inner CLI rejected or could not start before creating
                # a response, preserve that failure without misclassifying it as success.
                child_error = str(child.get("stderr") or child.get("stdout") or
                                  "Order runner returned without a durable response.")
                response = build_architect_response(
                    order, request_sha256, status="execution_error",
                    execution_started=execute, queue_mutated=None if execute else False,
                    operator_flags=flags, error=child_error,
                )
                response["source_file"] = candidate.name
                _write_response(response_path, response)
            response["source_file"] = candidate.name
            # The response record is now durable; only the CLI stdout wrapper gets
            # spool metadata, so the saved evidence remains a single architect packet.
            return {
                "spool_status": "processed",
                "status": response.get("status", "unknown"),
                "input_file": candidate.name,
                "request_sha256": request_sha256,
                "response_file": response_path.name,
                "exit_code": int(child.get("exit_code", 1)),
                "response": response,
                "already_responded_skipped": already_responded,
                "skipped_inputs": skipped,
                "unreadable_inputs": unreadable,
            }
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if snapshot is not None:
                try:
                    snapshot.unlink()
                except OSError:
                    pass

    if unreadable or skipped:
        return {
            "spool_status": "blocked" if not already_responded else "idle",
            "status": "no_safe_pending_order",
            "processed": False,
            "already_responded_skipped": already_responded,
            "skipped_inputs": skipped,
            "unreadable_inputs": unreadable,
            "next_action": "Inspect the listed input files; no unsafe or oversized input was executed.",
        }
    return {
        "spool_status": "idle",
        "status": "no_pending_order",
        "processed": False,
        "already_responded_skipped": already_responded,
        "skipped_inputs": [],
        "unreadable_inputs": [],
        "next_action": "Place one new bounded JSON order in the inbox or exit; the spool is not a background worker.",
    }
