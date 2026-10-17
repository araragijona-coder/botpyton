"""Fail-closed result contracts for local PC-care reports; never retries operations."""
from __future__ import annotations

import json
from typing import Any

SUCCESS_STATUSES = {
    "collected", "completed", "measured", "scan_complete", "preview_complete",
    "ready_to_restore", "no_candidates", "restored", "no_files_restored", "diagnostic_only",
}
PARTIAL_STATUSES = {"partial", "partial_limit", "preview_partial", "scan_partial"}
FAILURE_STATUSES = {
    "unavailable", "unsupported_platform", "not_present", "timeout",
    "command_unavailable", "query_unavailable", "query_error", "parse_error",
    "invalid_output", "output_too_large", "untrusted_executable",
    "source_check_timeout", "source_untrusted", "integrity_error",
    "unsafe_audit_directory", "read_error", "unreadable_record",
    "audit_unavailable", "audit_write_failed", "restore_failed",
    "source_or_query_error", "blocked", "error", "fail",
}


def assess_operation_result(report: object) -> dict[str, Any]:
    """Assess a PC-care report contract, not the underlying hardware's health."""
    if not isinstance(report, dict):
        return {
            "outcome": "unknown", "exit_code": 3, "status": None,
            "retry_recommended": False,
            "next_action": "The command returned no structured report. Stop and inspect the error before trying again.",
            "checks": ["report_is_object:fail"],
        }

    status = report.get("status")
    checks: list[str] = []
    if not isinstance(status, str) or not status.strip():
        return {
            "outcome": "unknown", "exit_code": 3, "status": status,
            "retry_recommended": False,
            "next_action": "The report has no recognized status. Do not treat it as success; inspect the report and command implementation.",
            "checks": ["status_present:fail"],
        }

    if isinstance(report.get("return_code"), int) and report["return_code"] != 0:
        checks.append("provider_return_code:fail")
        return {
            "outcome": "failed", "exit_code": 1, "status": status,
            "retry_recommended": False,
            "next_action": "The underlying provider returned a non-zero code. Review its bounded error details; no automatic retry was attempted.",
            "checks": checks,
        }

    for count_key, list_key in (
        ("returned_count", "items"),
        ("physical_disks_count", "physical_disks"),
        ("windows_disks_count", "windows_disks"),
    ):
        expected = report.get(count_key)
        values = report.get(list_key)
        if expected is not None and isinstance(values, list):
            if not isinstance(expected, int) or isinstance(expected, bool) or expected != len(values):
                checks.append(f"{count_key}_matches_{list_key}:fail")
                return {
                    "outcome": "failed", "exit_code": 1, "status": status,
                    "retry_recommended": False,
                    "next_action": "The report's count does not match its returned records. Do not rely on the result; preserve the report for diagnosis.",
                    "checks": checks,
                }
            checks.append(f"{count_key}_matches_{list_key}:pass")

    if report.get("integrity_status") == "error":
        checks.append("integrity_status:fail")
        return {
            "outcome": "failed", "exit_code": 1, "status": status,
            "retry_recommended": False,
            "next_action": "Report integrity validation failed. Do not use the affected audit or manifest; inspect the integrity error first.",
            "checks": checks,
        }

    skipped = report.get("files_skipped")
    if (report.get("scan_limited") is True or report.get("output_truncated") is True
            or (isinstance(report.get("access_errors"), int) and report["access_errors"] > 0)
            or report.get("integrity_status") == "partial"
            or (status in {"preview_complete", "restored", "completed"}
                and isinstance(skipped, int) and skipped > 0)):
        checks.append("completeness:partial")
        return {
            "outcome": "partial", "exit_code": 2, "status": status,
            "retry_recommended": False,
            "next_action": "The report is incomplete or some records/actions were skipped. Review the skipped/error counts; do not interpret it as exhaustive or fully completed.",
            "checks": checks,
        }

    if status in PARTIAL_STATUSES:
        return {
            "outcome": "partial", "exit_code": 2, "status": status,
            "retry_recommended": False,
            "next_action": "The operation was bounded or incomplete. Review the report and narrow the selected folder/scope before rerunning manually.",
            "checks": ["status:partial"],
        }
    if status in SUCCESS_STATUSES:
        return {
            "outcome": "reported_success", "exit_code": 0, "status": status,
            "retry_recommended": False,
            "next_action": "The report passed structural checks. This does not prove hardware health or guarantee every provider record exists.",
            "checks": checks + ["status:recognized_success"],
        }
    if status in FAILURE_STATUSES:
        return {
            "outcome": "failed", "exit_code": 1, "status": status,
            "retry_recommended": False,
            "next_action": "The operation did not produce a usable complete result. Review the report's note/error and satisfy the stated prerequisite before rerunning.",
            "checks": checks + ["status:recognized_failure"],
        }
    return {
        "outcome": "unknown", "exit_code": 3, "status": status,
        "retry_recommended": False,
        "next_action": "This status is not in the reviewed result contract. Treat it as unknown and inspect it before relying on the output.",
        "checks": checks + ["status:unrecognized"],
    }


def print_pc_result(report: object) -> int:
    """Print a report plus its assessment and return a meaningful CLI exit code."""
    assessment = assess_operation_result(report)
    payload = dict(report) if isinstance(report, dict) else {"raw_result": repr(report)}
    payload["operation_assessment"] = assessment
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return int(assessment["exit_code"])
