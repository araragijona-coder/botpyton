"""Machine-readable, durable feedback for external architect orders.

Response files are exclusive-create outputs: existing files and symbolic links are
never overwritten. A valid in-progress record is fsynced before task execution so
an interrupted worker can be distinguished from a completed order.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MAX_ARCHITECT_RESPONSE_BYTES = 4 * 1024 * 1024


class ArchitectResponseError(ValueError):
    """Raised when an architect response cannot be represented or persisted safely."""


class ArchitectResponseWriter:
    """Own one new response path and durably update it without clobbering other files."""

    def __init__(self, path_text: str | Path):
        requested = Path(path_text).expanduser()
        if not requested.name or requested.name in {".", ".."}:
            raise ArchitectResponseError("Architect response path must name a file")
        try:
            parent = requested.parent.resolve(strict=True)
        except OSError as exc:
            raise ArchitectResponseError(
                "Architect response parent directory must already exist"
            ) from exc
        if not parent.is_dir():
            raise ArchitectResponseError("Architect response parent must be a directory")
        self.path = parent / requested.name
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise ArchitectResponseError(
                "Architect response target already exists; choose a new path so evidence is never overwritten"
            ) from exc
        except OSError as exc:
            raise ArchitectResponseError(f"Cannot create architect response: {exc}") from exc
        try:
            self._stream = os.fdopen(descriptor, "r+", encoding="utf-8", newline="\n")
        except Exception:
            os.close(descriptor)
            try:
                self.path.unlink()
            except OSError:
                pass
            raise
        self._closed = False

    def write(self, response: dict[str, Any]) -> None:
        if self._closed:
            raise ArchitectResponseError("Architect response writer is already closed")
        try:
            text = json.dumps(
                response, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            ) + "\n"
        except (TypeError, ValueError) as exc:
            raise ArchitectResponseError(f"Architect response is not valid JSON data: {exc}") from exc
        encoded_size = len(text.encode("utf-8"))
        if encoded_size > MAX_ARCHITECT_RESPONSE_BYTES:
            raise ArchitectResponseError(
                f"Architect response exceeds the {MAX_ARCHITECT_RESPONSE_BYTES}-byte limit"
            )
        try:
            self._stream.seek(0)
            self._stream.truncate()
            self._stream.write(text)
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except OSError as exc:
            raise ArchitectResponseError(f"Cannot persist architect response: {exc}") from exc

    def close(self) -> None:
        if not self._closed:
            try:
                self._stream.flush()
                os.fsync(self._stream.fileno())
            finally:
                self._stream.close()
                self._closed = True

    def __enter__(self) -> "ArchitectResponseWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def build_architect_response(
    order: dict[str, Any],
    request_sha256: str,
    *,
    status: str,
    execution_started: bool,
    queue_mutated: bool | None,
    operator_flags: dict[str, bool],
    execution: dict[str, Any] | None = None,
    tasks_for_review: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, correlated response; no field from the packet grants authority."""
    task_results = execution.get("tasks", []) if isinstance(execution, dict) else []
    if not isinstance(task_results, list):
        task_results = []
    attempts = execution.get("attempt_results", []) if isinstance(execution, dict) else []
    if not isinstance(attempts, list):
        attempts = []
    succeeded = sum(
        1 for item in task_results
        if isinstance(item, dict) and item.get("status") == "succeeded"
    )
    failed = sum(
        1 for item in task_results
        if isinstance(item, dict) and item.get("status") in {"failed", "blocked"}
    )

    if status == "needs_input":
        next_action = {
            "actor": "architect",
            "action": "answer_questions",
            "questions": list(order.get("questions", [])),
            "message": "Return a new order packet after resolving these questions; this packet ran no tasks.",
        }
    elif status == "validated_for_review":
        next_action = {
            "actor": "local_operator",
            "action": "review_and_authorize",
            "required_local_approvals": list(order.get("required_local_approvals", [])),
            "message": "Review the exact task payload and target project. Packet fields cannot grant execution permissions.",
        }
    elif status in {"authorization_required", "authorization_mismatch"}:
        next_action = {
            "actor": "local_operator",
            "action": "review_local_approvals",
            "message": (
                "No execution was started. Review the packet and use only the exact permission flags required by its actions."
            ),
        }
    elif status == "execution_in_progress":
        next_action = {
            "actor": "local_operator",
            "action": "inspect_or_resume",
            "message": "This durable record is a start marker, not a success claim. Inspect queue and per-task evidence before resubmission.",
        }
    elif status == "succeeded":
        next_action = {
            "actor": "architect",
            "action": "review_execution_evidence",
            "message": "Use the task statuses, verification results and report paths to decide whether a new order is warranted.",
        }
    else:
        next_action = {
            "actor": "architect",
            "action": "analyze_failure_evidence",
            "message": "Inspect the failure and verification evidence; produce a revised bounded order or request missing information. Do not blindly replay unchanged work.",
        }

    response: dict[str, Any] = {
        "response_type": "botpyton.architect_response",
        "schema_version": 1,
        "order_id": order.get("order_id"),
        "source": order.get("source", {}),
        "request_sha256": request_sha256,
        "status": status,
        "execution_started": execution_started,
        "queue_mutated": queue_mutated,
        "authorization": {
            "required_local_approvals": list(order.get("required_local_approvals", [])),
            "operator_flags": dict(operator_flags),
            "packet_can_grant_permission": False,
            "source_is_cryptographically_authenticated": False,
            "automatic_merge": False,
        },
        "evidence": {
            "task_count": len(task_results),
            "succeeded_task_count": succeeded,
            "failed_or_blocked_task_count": failed,
            "attempts_executed": (
                execution.get("attempts_executed", len(attempts))
                if isinstance(execution, dict) else 0
            ),
            "verification_required": (
                execution.get("verification_required", True)
                if isinstance(execution, dict) else status != "needs_input"
            ),
            "tasks": [
                {
                    "task_id": item.get("id"),
                    "status": item.get("status"),
                    "attempts": item.get("attempts"),
                    "report_path": item.get("report_path"),
                }
                for item in task_results if isinstance(item, dict)
            ],
            "attempt_results": attempts,
        },
        "execution": execution,
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
    if tasks_for_review is not None:
        response["tasks_for_review"] = tasks_for_review
    if error:
        response["error"] = str(error)[:2_000]
    return response



def build_rejected_architect_response(
    request_sha256: str,
    source_file_name: str,
    error: str,
    operator_flags: dict[str, bool],
) -> dict[str, Any]:
    """Response for invalid JSON or a packet that failed the strict contract."""
    return {
        "response_type": "botpyton.architect_response",
        "schema_version": 1,
        "order_id": None,
        "source": {"source_file": source_file_name},
        "source_file": source_file_name,
        "request_sha256": request_sha256,
        "status": "rejected_invalid_order",
        "execution_started": False,
        "queue_mutated": False,
        "authorization": {
            "required_local_approvals": [],
            "operator_flags": dict(operator_flags),
            "packet_can_grant_permission": False,
            "source_is_cryptographically_authenticated": False,
            "automatic_merge": False,
        },
        "evidence": {
            "task_count": 0,
            "succeeded_task_count": 0,
            "failed_or_blocked_task_count": 0,
            "attempts_executed": 0,
            "verification_required": False,
            "tasks": [],
            "attempt_results": [],
        },
        "execution": None,
        "error": str(error)[:2_000],
        "next_action": {
            "actor": "architect",
            "action": "repair_order_packet",
            "message": "Correct the JSON or strict order-contract error, then submit it as a new packet. No task ran.",
        },
        "next_step": "Correct the rejected packet. No task ran.",
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
        "automatic_merge": False,
        "safety": {
            "botpyton_ai_enabled": False,
            "external_ai_api_called": False,
            "botpyton_network_requests_made": False,
            "packet_can_grant_permission": False,
            "automatic_merge": False,
        },
    }
