"""On-demand, one-order inbox/outbox for structured architect packets.

No watcher, daemon, network client, shell dispatch, or permission inference is added.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .application_cache import _is_link_or_reparse
from .architect_order_contract import ArchitectOrderError, validate_architect_order
from .architect_order_response import (
    ArchitectResponseWriter,
    build_architect_response,
    build_rejected_architect_response,
)
from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .memory import JsonMemory, MemoryError
from .queue import QueueError, TaskQueue
from .workspace import _git

MAX_SPOOL_DIRECTORY_ENTRIES = 5_000
MAX_TASK_REPORT_BYTES = 1_000_000
_TASK_REPORT_PATH = re.compile(r"^\.cerebro/reports/[0-9a-f]{16}\.json$")
MAX_SPOOL_ORDERS = 500
MAX_ORDER_BYTES = 600_000
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
    valid_statuses = {
        "validated_for_review", "needs_input", "execution_in_progress", "recovery_required",
        "succeeded", "failed", "blocked", "incomplete", "execution_error",
        "authorization_required", "authorization_mismatch", "rejected_invalid_order",
    }
    if (
        not isinstance(value, dict)
        or value.get("response_type") != "botpyton.architect_response"
        or value.get("schema_version") != 1
        or value.get("request_sha256") != expected_sha256
        or value.get("status") not in valid_statuses
        or type(value.get("execution_started")) is not bool
        or value.get("queue_mutated") not in (True, False, None)
        or not isinstance(value.get("authorization"), dict)
        or not isinstance(value.get("evidence"), dict)
        or not isinstance(value.get("next_action"), dict)
    ):
        raise ArchitectSpoolError(
            f"Existing response does not match the expected order hash/schema: {path.name}"
        )
    return value


def _registered_workspace_root(
    project_root: str | Path,
    previous_response: dict[str, Any],
    binding_mismatches: list[str],
) -> tuple[Path | None, dict[str, Any]]:
    """Return a report root only when the response's worktree is registered by Git."""
    try:
        source = Path(project_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, {"status": "source_root_unavailable", "error": type(exc).__name__}
    if not source.is_dir():
        return None, {"status": "source_root_unavailable", "error": "not_a_directory"}

    workspace_claim = previous_response.get("workspace")
    if not isinstance(workspace_claim, dict) or not workspace_claim.get("workspace_path"):
        return source, {"status": "source_root", "registered": True}
    if binding_mismatches:
        return None, {
            "status": "response_binding_mismatch",
            "registered": False,
            "claim_trusted": False,
        }

    candidate_text = workspace_claim.get("workspace_path")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        return None, {"status": "invalid_workspace_claim", "registered": False}
    candidate_input = Path(candidate_text).expanduser()
    try:
        if _is_link_or_reparse(candidate_input):
            return None, {"status": "workspace_link_blocked", "registered": False}
        candidate = candidate_input.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, {"status": "workspace_unavailable", "registered": False, "error": type(exc).__name__}
    if not candidate.is_dir() or candidate == source:
        return None, {"status": "workspace_not_isolated", "registered": False}

    top = _git(source, ["rev-parse", "--show-toplevel"])
    listed = _git(source, ["worktree", "list", "--porcelain"])
    if top["exit_code"] != 0 or listed["exit_code"] != 0:
        return None, {"status": "git_worktree_registry_unavailable", "registered": False}
    try:
        if Path(top["stdout"].strip()).resolve() != source:
            return None, {"status": "source_root_not_repository_root", "registered": False}
    except (OSError, RuntimeError, ValueError):
        return None, {"status": "source_root_not_repository_root", "registered": False}

    for block in listed["stdout"].split("\n\n"):
        lines = block.splitlines()
        worktree_line = next((line[len("worktree "):] for line in lines if line.startswith("worktree ")), None)
        if worktree_line is None:
            continue
        try:
            registered_path = Path(worktree_line).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if registered_path != candidate:
            continue
        branch_ref = next((line[len("branch "):] for line in lines if line.startswith("branch ")), "")
        if not branch_ref.startswith("refs/heads/botpyton/"):
            return None, {"status": "workspace_branch_not_allowlisted", "registered": False}
        current_branch = _git(candidate, ["symbolic-ref", "--short", "HEAD"])
        current_head = _git(candidate, ["rev-parse", "HEAD"])
        if (
            current_branch["exit_code"] != 0
            or current_head["exit_code"] != 0
            or current_branch["stdout"].strip() != branch_ref.removeprefix("refs/heads/")
            or not re.fullmatch(r"[0-9a-f]{40}", current_head["stdout"].strip())
        ):
            return None, {"status": "workspace_identity_mismatch", "registered": False}
        return candidate, {
            "status": "registered_git_worktree",
            "registered": True,
            "branch": current_branch["stdout"].strip(),
            "head_sha": current_head["stdout"].strip(),
        }
    return None, {"status": "workspace_not_registered", "registered": False}


def _inspect_task_report(
    report_root: Path | None,
    task_id: str,
    report_path: object,
) -> dict[str, Any]:
    """Read one strictly allowlisted task report and verify task/result/acceptance identity."""
    if report_root is None:
        return {"status": "report_root_unavailable", "verified_success": False}
    if not isinstance(report_path, str) or not report_path.strip():
        return {"status": "report_path_missing", "verified_success": False}
    normalized = report_path.replace("\\", "/")
    if not _TASK_REPORT_PATH.fullmatch(normalized):
        return {"status": "unsafe_report_reference", "verified_success": False}
    relative = PurePosixPath(normalized)
    if relative.as_posix() != normalized or ".." in relative.parts:
        return {"status": "unsafe_report_reference", "verified_success": False}

    target = report_root
    for part in relative.parts:
        target = target / part
        if _is_link_or_reparse(target):
            return {"status": "report_link_blocked", "verified_success": False}
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"status": "report_missing", "verified_success": False, "path": normalized}
    except OSError as exc:
        return {"status": "report_unreadable", "verified_success": False, "error": type(exc).__name__}
    if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > MAX_TASK_REPORT_BYTES:
        return {"status": "report_invalid_file", "verified_success": False, "path": normalized}
    try:
        raw = read_bounded_bytes(
            target, limit_bytes=MAX_TASK_REPORT_BYTES, label="task recovery report"
        )
        value = json.loads(raw.decode("utf-8"))
        enforce_json_depth(value, label="task recovery report")
    except (BoundedReadError, UnicodeError, json.JSONDecodeError, RecursionError, OSError) as exc:
        return {
            "status": "report_invalid_json",
            "verified_success": False,
            "path": normalized,
            "error": type(exc).__name__,
        }
    if not isinstance(value, dict) or value.get("task_id") != task_id:
        return {
            "status": "report_task_id_mismatch",
            "verified_success": False,
            "path": normalized,
            "report_task_id": value.get("task_id") if isinstance(value, dict) else None,
        }

    verification = value.get("verification")
    checks = verification.get("checks") if isinstance(verification, dict) else None
    all_checks_passed = (
        isinstance(checks, list) and bool(checks)
        and all(isinstance(check, dict) and check.get("ok") is True for check in checks)
    )
    succeeded = (
        value.get("status") == "succeeded"
        and value.get("ok") is True
        and isinstance(verification, dict)
        and verification.get("all_passed") is True
        and all_checks_passed
    )
    failed_checks = [
        {
            "type": check.get("type"),
            "reason": str(check.get("reason", ""))[:300],
        }
        for check in checks if isinstance(check, dict) and check.get("ok") is not True
    ] if isinstance(checks, list) else []
    return {
        "status": "verified_success" if succeeded else (
            "report_failure_or_unverified" if value.get("status") != "succeeded" or value.get("ok") is not True
            else "verification_missing_or_failed"
        ),
        "verified_success": succeeded,
        "path": normalized,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "report_status": value.get("status"),
        "report_ok": value.get("ok") is True,
        "failed_action": str(value.get("failed_action") or "")[:200] or None,
        "error": str(value.get("error") or "")[:1_000] or None,
        "verification_status": verification.get("status") if isinstance(verification, dict) else None,
        "verification_all_passed": verification.get("all_passed") if isinstance(verification, dict) else None,
        "verification_checks": [
            {
                "type": check.get("type"),
                "ok": check.get("ok") is True,
                "reason": str(check.get("reason", ""))[:300],
            }
            for check in checks[:20] if isinstance(check, dict)
        ] if isinstance(checks, list) else [],
        "failed_checks": failed_checks[:20],
    }


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
            previous_response = _read_existing_response(response_path, request_sha256)
            if previous_response["status"] == "execution_in_progress":
                # Derive task identity from the exact input bytes, not from the
                # mutable response file. A matching request hash is correlation,
                # not an authenticity signature over the response contents.
                try:
                    original_packet = json.loads(raw.decode("utf-8"))
                    enforce_json_depth(original_packet, label="architect inbox packet")
                    canonical_order = validate_architect_order(original_packet)
                except (
                    UnicodeError, json.JSONDecodeError, RecursionError,
                    BoundedReadError, ArchitectOrderError,
                ) as exc:
                    raise ArchitectSpoolError(
                        "Cannot safely reconcile an in-progress response against its original order"
                    ) from exc
                if canonical_order["status"] != "ready":
                    raise ArchitectSpoolError(
                        "An in-progress response is bound to an order that is no longer executable"
                    )

                task_ids = list(canonical_order["task_ids"])
                recorded_validation = previous_response.get("validation")
                recorded_authorization = previous_response.get("authorization")
                binding_mismatches: list[str] = []
                if previous_response.get("order_id") != canonical_order["order_id"]:
                    binding_mismatches.append("order_id")
                if previous_response.get("source") != canonical_order["source"]:
                    binding_mismatches.append("source")
                if (
                    not isinstance(recorded_validation, dict)
                    or recorded_validation.get("task_ids") != task_ids
                ):
                    binding_mismatches.append("validation.task_ids")
                if (
                    not isinstance(recorded_validation, dict)
                    or recorded_validation.get("required_local_approvals")
                    != canonical_order["required_local_approvals"]
                ):
                    binding_mismatches.append("validation.required_local_approvals")
                if (
                    not isinstance(recorded_authorization, dict)
                    or recorded_authorization.get("required_local_approvals")
                    != canonical_order["required_local_approvals"]
                ):
                    binding_mismatches.append("authorization.required_local_approvals")
                if (
                    not isinstance(recorded_authorization, dict)
                    or recorded_authorization.get("operator_flags") != flags
                ):
                    binding_mismatches.append("authorization.operator_flags")
                if previous_response.get("execution_started") is not True:
                    binding_mismatches.append("execution_started")
                if previous_response.get("queue_mutated") is not None:
                    binding_mismatches.append("queue_mutated")
                if previous_response.get("execution") is not None:
                    binding_mismatches.append("execution")

                report_root, report_root_evidence = _registered_workspace_root(
                    project_root, previous_response, binding_mismatches
                )
                queue_snapshot: dict[str, Any] = {
                    "status": "unavailable",
                    "tasks": [],
                }
                try:
                    task_records = TaskQueue(JsonMemory(memory_path)).list_tasks()
                    indexed = {
                        record["id"]: record for record in task_records
                        if isinstance(record, dict) and isinstance(record.get("id"), str)
                    }
                    queue_tasks: list[dict[str, Any]] = []
                    for task_id in task_ids:
                        record = indexed.get(task_id)
                        if record is None:
                            queue_tasks.append({
                                "task_id": task_id,
                                "status": "not_found",
                                "report_evidence": {"status": "no_queue_record", "verified_success": False},
                            })
                            continue
                        result = record.get("result")
                        report_path = result.get("report_path") if isinstance(result, dict) else None
                        report_evidence = _inspect_task_report(report_root, task_id, report_path)
                        queue_tasks.append({
                            "task_id": task_id,
                            "status": record.get("status"),
                            "attempts": record.get("attempts"),
                            "last_error": str(record.get("last_error") or "")[:1_000] or None,
                            "report_path": report_path,
                            "queue_verification_recorded": (
                                isinstance(result, dict) and isinstance(result.get("verification"), dict)
                            ),
                            "report_evidence": report_evidence,
                        })
                    queue_snapshot = {
                        "status": "inspected",
                        "task_count": len(task_ids),
                        "tasks": queue_tasks,
                        "all_reports_verified": bool(queue_tasks) and all(
                            item.get("status") == "succeeded"
                            and isinstance(item.get("report_evidence"), dict)
                            and item["report_evidence"].get("verified_success") is True
                            for item in queue_tasks
                        ),
                        "report_root": report_root_evidence,
                    }
                except (OSError, MemoryError, QueueError, ValueError, TypeError) as exc:
                    queue_snapshot = {
                        "status": "unavailable",
                        "tasks": [],
                        "error": f"{type(exc).__name__}: {exc}"[:1_000],
                    }

                states = [item.get("status") for item in queue_snapshot.get("tasks", [])]
                all_reports_verified = queue_snapshot.get("all_reports_verified") is True
                if binding_mismatches:
                    next_action = {
                        "actor": "local_operator",
                        "action": "inspect_response_integrity",
                        "message": (
                            "The stored in-progress response does not match the validated order and current authorization profile. "
                            "No task was replayed. Preserve the original response, inspect queue/report evidence, and do not treat "
                            "the response's mismatched fields as trustworthy."
                        ),
                    }
                elif (
                    task_ids and queue_snapshot.get("status") == "inspected"
                    and states == ["succeeded"] * len(task_ids) and all_reports_verified
                ):
                    next_action = {
                        "actor": "local_operator",
                        "action": "verify_reports_and_close_interrupted_run",
                        "message": (
                            "Every order task is succeeded and its bounded report confirms task identity plus passing acceptance checks. "
                            "The original response was not finalized; review the report hashes and workspace diff before closing the interrupted run. "
                            "This report audit is evidence, not a cryptographic signature over local files."
                        ),
                    }
                elif (
                    task_ids and queue_snapshot.get("status") == "inspected"
                    and states == ["succeeded"] * len(task_ids)
                ):
                    next_action = {
                        "actor": "local_operator",
                        "action": "inspect_task_report_evidence",
                        "message": (
                            "The queue marks tasks succeeded but one or more bounded task reports are missing, inconsistent, or not verified. "
                            "Inspect evidence.report_evidence and the registered workspace; do not treat queue status alone as proof of success."
                        ),
                    }
                elif "running" in states:
                    next_action = {
                        "actor": "local_operator",
                        "action": "inspect_running_task",
                        "message": (
                            "At least one task remains running. Do not replay the order. Inspect its task report and queue record; "
                            "use the existing queue recovery procedure only after determining whether side effects completed."
                        ),
                    }
                elif queue_snapshot.get("status") != "inspected":
                    next_action = {
                        "actor": "local_operator",
                        "action": "repair_or_inspect_queue",
                        "message": (
                            "The task queue could not be read safely. Preserve the original response and repair/inspect queue integrity "
                            "before any retry. The spool did not rerun the order."
                        ),
                    }
                elif any(state in {"failed", "blocked"} for state in states):
                    next_action = {
                        "actor": "architect",
                        "action": "create_revised_order_from_failure_evidence",
                        "message": (
                            "One or more order tasks failed or were blocked. Use the bounded report failure fields and verification evidence "
                            "to diagnose the cause, then submit a revised order with a new order_id and task IDs where work must change. "
                            "Do not retry the same failed task payload blindly; local approvals remain separate."
                        ),
                    }
                else:
                    next_action = {
                        "actor": "local_operator",
                        "action": "reconcile_queue_before_retry",
                        "message": (
                            "The original execution has no final response and the queue is incomplete or unresolved. Review each task state, "
                            "report hashes, verification evidence, and possible side effects before deciding whether to create a revised order."
                        ),
                    }

                recovery_core = {
                    "response_type": "botpyton.architect_response",
                    "schema_version": 1,
                    "order_id": canonical_order["order_id"],
                    "source": canonical_order["source"],
                    "source_file": candidate.name,
                    "request_sha256": request_sha256,
                    "status": "recovery_required",
                    "execution_started": True,
                    # The old response is mutable and has failed binding checks;
                    # queue mutation must therefore remain explicitly unknown.
                    "queue_mutated": None,
                    "authorization": {
                        "required_local_approvals": canonical_order["required_local_approvals"],
                        "operator_flags": flags,
                        "packet_can_grant_permission": False,
                        "source_is_cryptographically_authenticated": False,
                        "automatic_merge": False,
                    },
                    "evidence": {
                        "original_status": previous_response.get("status"),
                        "original_response_file": response_path.name,
                        "task_ids_from_order": task_ids,
                        "response_binding_status": "mismatch" if binding_mismatches else "matched",
                        "response_binding_mismatches": binding_mismatches,
                        # This is a locator copied from a local response file, not a
                        # signed attestation of which worktree was actually used.
                        "workspace_claim_trusted": False,
                        "workspace_claim_from_original_response": (
                            previous_response.get("workspace")
                            if not binding_mismatches and isinstance(previous_response.get("workspace"), dict)
                            else None
                        ),
                        "report_root_validation": report_root_evidence,
                        "queue_snapshot": queue_snapshot,
                    },
                    "execution": None,
                    "next_action": next_action,
                    "next_step": next_action["message"],
                    "ai_enabled": False,
                    "external_ai_apis_enabled": False,
                    "automatic_merge": False,
                    "safety": {
                        "botpyton_ai_enabled": False,
                        "external_ai_api_called": False,
                        "botpyton_network_requests_made": False,
                        "project_code_may_access_network_when_explicitly_authorized": True,
                        "automatic_merge": False,
                    },
                }
                snapshot_json = json.dumps(
                    queue_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()[:16]
                recovery_path = outbox / (
                    f"order-{request_sha256}.{profile}.recovery-{snapshot_hash}.response.json"
                )
                try:
                    recovery_path.lstat()
                    recovery_exists = True
                except FileNotFoundError:
                    recovery_exists = False
                except OSError as exc:
                    raise ArchitectSpoolError(
                        f"Cannot inspect recovery output {recovery_path.name}: {exc}"
                    ) from exc
                if recovery_exists:
                    recovery_response = _read_existing_response(recovery_path, request_sha256)
                    if recovery_response.get("status") != "recovery_required":
                        raise ArchitectSpoolError(
                            f"Recovery output has an unexpected status: {recovery_path.name}"
                        )
                else:
                    _write_response(recovery_path, recovery_core)
                    recovery_response = _read_existing_response(recovery_path, request_sha256)

                return {
                    "spool_status": "recovery_required",
                    "status": "recovery_required",
                    "processed": False,
                    "execution_replayed": False,
                    "input_file": candidate.name,
                    "request_sha256": request_sha256,
                    "response_file": response_path.name,
                    "recovery_report_file": recovery_path.name,
                    "response": recovery_response,
                    "already_responded_skipped": already_responded,
                    "skipped_inputs": skipped,
                    "unreadable_inputs": unreadable,
                    "next_action": next_action,
                }
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
                prefix="botpyton-order-", suffix=".json"
            )
            snapshot = Path(snapshot_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                child = run_order(snapshot, response_path, flags)
            except Exception as exc:
                child = {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"{type(exc).__name__}: {exc}",
                }
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
