"""Build a bounded, deterministic failure hand-off for an external architect.

This module only transforms local evidence into data. It never executes, retries,
authorizes, sends, or publishes an order.
"""
from __future__ import annotations

import re
from typing import Any

MAX_REVISION_TASKS = 25
MAX_REVISION_CHECKS = 10
MAX_TEXT_LENGTH = 240
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_BRANCH = re.compile(r"^botpyton/[A-Za-z0-9._/-]{1,120}$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|cookie|"
    r"client[_-]?secret|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_URL_CREDENTIALS = re.compile(r"(://[^:/\s]+:)[^@/\s]+@")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def _bounded_text(value: object, limit: int = MAX_TEXT_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _GITHUB_TOKEN.sub("[REDACTED TOKEN]", value)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    value = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in value)
    value = " ".join(value.split())[:limit]
    return value or None


def _digest(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _task_id(value: object) -> str | None:
    return value if isinstance(value, str) and _TASK_ID.fullmatch(value) else None


def _failed_checks(report: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, Any]] = []
    for key in ("failed_checks", "verification_checks"):
        values = report.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or item.get("ok") is True:
                continue
            candidates.append(item)
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        check_type = _bounded_text(item.get("type"), 80) or "unknown"
        reason = _bounded_text(item.get("reason"), 180) or "No bounded reason recorded"
        identity = (check_type, reason)
        if identity in seen:
            continue
        seen.add(identity)
        output.append({"type": check_type, "reason": reason})
        if len(output) >= MAX_REVISION_CHECKS:
            break
    return output


def _workspace_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unavailable", "registered": False}
    status = _bounded_text(value.get("status"), 60) or "unknown"
    registered = value.get("registered") is True
    summary: dict[str, Any] = {"status": status, "registered": registered}
    branch = value.get("branch")
    if isinstance(branch, str) and _BRANCH.fullmatch(branch):
        summary["branch"] = branch
    head_sha_value = value.get("head_sha")
    if isinstance(head_sha_value, str) and _GIT_SHA.fullmatch(head_sha_value):
        summary["head_sha"] = head_sha_value
    # Do not copy paths, patch text, full diffs, or unverified workspace claims.
    return summary


def build_revision_context(
    *,
    order_id: object,
    request_sha256: object,
    queue_snapshot: object,
    report_root_evidence: object = None,
) -> dict[str, Any]:
    """Return a deterministic, allowlisted packet for architect-led revision.

    Raw order contents, local paths, complete logs and full report bodies are never
    copied. The context cannot grant approval or trigger an execution.
    """
    tasks: list[dict[str, Any]] = []
    if isinstance(queue_snapshot, dict):
        records = queue_snapshot.get("tasks")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                status = record.get("status")
                if status not in {"failed", "blocked"}:
                    continue
                task_id = _task_id(record.get("task_id"))
                if task_id is None:
                    continue
                report = record.get("report_evidence")
                if not isinstance(report, dict):
                    report = {}
                attempts = record.get("attempts")
                if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                    attempts = None
                elif attempts > 1000:
                    attempts = 1000
                task: dict[str, Any] = {
                    "task_id": task_id,
                    "queue_status": status,
                    "attempts": attempts,
                    "task_payload_sha256": _digest(record.get("task_payload_sha256")),
                    "report_status": _bounded_text(report.get("status"), 60) or "unavailable",
                    "report_sha256": _digest(report.get("sha256")),
                    "failed_action": _bounded_text(report.get("failed_action"), 120),
                    "error_summary": _bounded_text(report.get("error")) or _bounded_text(record.get("last_error")),
                    "failed_checks": _failed_checks(report),
                }
                tasks.append(task)
                if len(tasks) >= MAX_REVISION_TASKS:
                    break

    context: dict[str, Any] = {
        "context_type": "botpyton.architect_revision_context",
        "schema_version": 1,
        "source_order": {
            "order_id": order_id if isinstance(order_id, str) and _ORDER_ID.fullmatch(order_id) else None,
            "request_sha256": _digest(request_sha256),
        },
        "tasks": tasks,
        "workspace": _workspace_summary(report_root_evidence),
        "revision_requirements": {
            "new_order_id_required": True,
            "new_task_ids_for_changed_work": True,
            "changed_task_payload_required": True,
            "unchanged_failed_payload_must_not_be_replayed": True,
            "human_review_required": True,
            "local_approvals_inherited": False,
        },
        "execution_policy": {
            "may_execute": False,
            "may_retry": False,
            "may_change_queue": False,
            "may_publish_or_merge": False,
            "botpyton_ai_enabled": False,
            "external_ai_api_called": False,
            "secrets_or_full_logs_included": False,
        },
        "omitted_data": [
            "raw_order",
            "full_task_reports",
            "complete_logs_or_stdout",
            "local_filesystem_paths",
            "patch_bodies_and_full_diffs",
            "credentials_and_environment_values",
        ],
    }
    return context
