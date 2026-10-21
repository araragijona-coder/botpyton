"""Read-only reconciliation of interrupted, deterministic GitHub publications.

This module never writes to GitHub. It validates a plan before issuing any request,
then uses bounded GET-only probes and fails closed whenever GitHub cannot provide
enough consistent evidence to safely retry the same plan.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.parse
from typing import Any

from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .github_ops import GitHubAPIError, GitHubClient, GitHubOpsError, validate_change_plan

_BRANCH_PAGE_SIZE = 100
_BRANCH_MAX_PAGES = 10
_PULL_MAX_PAGES = 5
_COMPARE_FILE_LIMIT = 300
_MAX_PLAN_BYTES = 600_000


class _InspectionStop(Exception):
    def __init__(
        self,
        code: str,
        recommendation: str = "manual_review_required",
        *,
        http_status: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.recommendation = recommendation
        self.http_status = http_status


def _http_code(status: int) -> str:
    if status == 401:
        return "authentication_invalid_or_expired"
    if status == 403:
        return "permission_or_policy_blocked"
    if status == 404:
        return "resource_not_confirmed_http_404"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "github_service_unavailable"
    return "github_request_rejected"


def _decode_file(response: Any) -> str:
    if (
        not isinstance(response, dict)
        or response.get("type") != "file"
        or response.get("encoding") != "base64"
        or not isinstance(response.get("content"), str)
    ):
        raise _InspectionStop("unexpected_file_response_shape")
    encoded = response["content"]
    # Plan files are <= 200 KB. An oversized response is deliberately not decoded.
    if len(encoded) > 550_000:
        raise _InspectionStop("remote_file_response_exceeds_limit")
    try:
        return base64.b64decode(encoded, validate=False).decode("utf-8")
    except (ValueError, UnicodeError):
        raise _InspectionStop("remote_file_content_not_valid_utf8") from None


def _read_remote_file(client: GitHubClient, path: str, ref: str, get) -> str:
    endpoint = client._endpoint(
        "/contents/" + urllib.parse.quote(path, safe="/")
        + "?" + urllib.parse.urlencode({"ref": ref})
    )
    try:
        response = get(endpoint)
    except _InspectionStop as stop:
        if stop.http_status == 404:
            # GitHub can conceal unavailable resources behind 404. Never call it absent.
            raise _InspectionStop("file_unreadable_or_missing_http_404", http_status=404) from None
        raise
    return _decode_file(response)


def _list_branches(client: GitHubClient, wanted: str, get) -> tuple[str, int]:
    """Find a branch or prove absence only after a bounded, complete branch listing."""
    for page in range(1, _BRANCH_MAX_PAGES + 1):
        endpoint = client._endpoint("/branches?" + urllib.parse.urlencode({
            "per_page": _BRANCH_PAGE_SIZE, "page": page
        }))
        records = get(endpoint)
        if not isinstance(records, list) or len(records) > _BRANCH_PAGE_SIZE:
            raise _InspectionStop("branch_inventory_response_invalid")
        for item in records:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise _InspectionStop("branch_inventory_entry_invalid")
            if item["name"] == wanted:
                return "found", page
        if len(records) < _BRANCH_PAGE_SIZE:
            return "absent_in_complete_inventory", page
    raise _InspectionStop("branch_inventory_limit_reached")


def _list_matching_pull_requests(
    client: GitHubClient, branch_name: str, base_branch: str, get
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    head = f"{client.owner}:{branch_name}"
    for page in range(1, _PULL_MAX_PAGES + 1):
        endpoint = client._endpoint("/pulls?" + urllib.parse.urlencode({
            "state": "all",
            "head": head,
            "base": base_branch,
            "per_page": 100,
            "page": page,
        }))
        records = get(endpoint)
        if not isinstance(records, list) or len(records) > 100:
            raise _InspectionStop("pull_request_inventory_response_invalid")
        for item in records:
            if not isinstance(item, dict):
                raise _InspectionStop("pull_request_record_invalid")
            head_info = item.get("head")
            base_info = item.get("base")
            if not isinstance(head_info, dict) or not isinstance(base_info, dict):
                raise _InspectionStop("pull_request_refs_invalid")
            if (
                head_info.get("ref") == branch_name
                and base_info.get("ref") == base_branch
            ):
                state = item.get("state")
                if state not in {"open", "closed"}:
                    raise _InspectionStop("pull_request_state_unexpected")
                number = item.get("number")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    raise _InspectionStop("pull_request_number_invalid")
                details: dict[str, Any] = {
                    "number": number,
                    "state": state,
                    "merged": item.get("merged") if isinstance(item.get("merged"), bool) else None,
                }
                # Do not copy private title/body or raw API records to the report.
                url = item.get("html_url")
                if isinstance(url, str) and url.startswith("https://github.com/"):
                    details["url"] = url[:500]
                found.append(details)
        if len(records) < 100:
            return found
    raise _InspectionStop("pull_request_inventory_limit_reached")


def _base_plan_effect(
    client: GitHubClient, base_branch: str, changes: dict[str, str], get
) -> str:
    """Check whether retrying the declared contents will make a real tree change."""
    has_confirmed_difference = False
    unknown_paths: list[str] = []
    for path, expected in changes.items():
        try:
            actual = _read_remote_file(client, path, base_branch, get)
        except _InspectionStop as stop:
            if stop.code == "file_unreadable_or_missing_http_404":
                unknown_paths.append(path)
                continue
            raise
        if actual != expected:
            has_confirmed_difference = True
    if has_confirmed_difference:
        return "effective_change_confirmed"
    if unknown_paths:
        raise _InspectionStop("base_plan_effect_unknown_for_some_paths")
    return "plan_has_no_effective_change"


def _set_evidence(report: dict[str, Any], key: str, status: str, **details: Any) -> None:
    report["evidence"][key] = {"status": status, **details}


def inspect_github_publication(
    plan_data: Any,
    *,
    client: GitHubClient,
) -> dict[str, Any]:
    """Inspect an interrupted publication without any write request.

    A valid plan is canonicalized before the first request. Every request made by
    this function uses GET; no automatic retry or recovery operation is performed.
    """
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "blocked",
        "recommendation": "blocked",
        "repository": client.repository,
        "plan_validated": False,
        "branch": {"status": "not_inspected"},
        "pull_request": {"status": "not_inspected"},
        "evidence": {},
        "trust_limits": [
            "This is a point-in-time read-only inspection, not a reservation or lock on remote refs.",
            "HTTP 404 alone never proves that a branch, file, repository, or pull request is absent.",
            "Safe-to-retry applies only to this exact canonical plan fingerprint; do not edit the plan before retrying.",
            "GitHub visibility, policy, permissions and branch protection can change after this report.",
            "No remote write was tested or attempted; no publication is performed by this inspector.",
        ],
        "network_used": False,
        "writes_performed": False,
        "secrets_disclosed": False,
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }

    # CRITICAL ORDERING: plan validation completes before the first network request.
    try:
        plan = validate_change_plan(plan_data)
    except (GitHubOpsError, TypeError, ValueError, UnicodeError):
        report["failure_code"] = "plan_validation_failed"
        report["evidence"]["plan"] = {"status": "blocked"}
        return report
    report["plan_validated"] = True
    report["task_id"] = plan.task_id
    report["plan_fingerprint"] = plan.fingerprint
    report["branch"]["name"] = plan.branch_name
    report["base_branch"] = plan.base_branch

    def get(endpoint: str) -> Any:
        report["network_used"] = True
        try:
            return client.request("GET", endpoint)
        except GitHubAPIError as exc:
            raise _InspectionStop(
                _http_code(exc.status),
                "blocked" if exc.status in {401, 403} else "manual_review_required",
                http_status=exc.status,
            ) from None
        except (GitHubOpsError, OSError, TimeoutError, ValueError):
            raise _InspectionStop("github_transport_or_response_error") from None

    try:
        repository_info = get(client._endpoint(""))
        if not isinstance(repository_info, dict):
            raise _InspectionStop("repository_metadata_invalid", "blocked")
        default_branch = repository_info.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise _InspectionStop("repository_default_branch_unknown", "blocked")
        if default_branch != plan.base_branch:
            raise _InspectionStop("plan_base_is_not_repository_default_branch", "blocked")
        _set_evidence(report, "repository_metadata", "verified",
                      default_branch=default_branch)

        base_ref = get(client._endpoint(
            "/git/ref/heads/" + urllib.parse.quote(plan.base_branch, safe="/")
        ))
        if (
            not isinstance(base_ref, dict)
            or not isinstance(base_ref.get("object"), dict)
            or not isinstance(base_ref["object"].get("sha"), str)
            or not base_ref["object"]["sha"]
        ):
            raise _InspectionStop("base_ref_response_invalid", "blocked")
        base_sha = base_ref["object"]["sha"]
        _set_evidence(report, "base_ref", "verified", sha=base_sha)

        ref_endpoint = client._endpoint(
            "/git/ref/heads/" + urllib.parse.quote(plan.branch_name, safe="/")
        )
        branch_absent = False
        try:
            ref = get(ref_endpoint)
        except _InspectionStop as stop:
            if stop.http_status != 404:
                raise
            # A single ref lookup returning 404 is ambiguous. Cross-check with a complete
            # bounded branch inventory; do not treat the 404 itself as absence evidence.
            _set_evidence(report, "branch_ref", "http_404_ambiguous")
            inventory_status, pages = _list_branches(client, plan.branch_name, get)
            if inventory_status != "absent_in_complete_inventory":
                raise _InspectionStop("branch_ref_and_inventory_disagree")
            branch_absent = True
            report["branch"].update({
                "status": "absent_in_complete_inventory",
                "head_sha": None,
            })
            _set_evidence(report, "branch_inventory", "complete",
                          pages_read=pages, target_found=False)
        else:
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get("object"), dict)
                or not isinstance(ref["object"].get("sha"), str)
                or not ref["object"]["sha"]
            ):
                raise _InspectionStop("branch_ref_response_invalid")
            initial_sha = ref["object"]["sha"]
            report["branch"].update({"status": "present", "head_sha": initial_sha})
            _set_evidence(report, "branch_ref", "verified", head_sha=initial_sha)

        if branch_absent:
            effect = _base_plan_effect(client, plan.base_branch, plan.changes, get)
            _set_evidence(report, "base_plan_effect", effect)
            pulls = _list_matching_pull_requests(client, plan.branch_name, plan.base_branch, get)
            report["pull_request"] = {"status": "absent" if not pulls else "present"}
            if pulls:
                report["pull_request"].update({"matching_count": len(pulls), "items": pulls[:5]})
                raise _InspectionStop("pull_request_exists_without_confirmed_branch")
            _set_evidence(report, "pull_request", "absent_in_complete_query")
            final_status, final_pages = _list_branches(client, plan.branch_name)
            if final_status != "absent_in_complete_inventory":
                raise _InspectionStop("branch_appeared_during_inspection")
            _set_evidence(report, "branch_inventory_final", "complete",
                          pages_read=final_pages, target_found=False)
            if effect != "effective_change_confirmed":
                raise _InspectionStop(effect, "manual_review_required")
            report["status"] = "inspected"
            report["recommendation"] = "safe_to_retry_same_plan"
            report["reason_code"] = "branch_absence_confirmed_and_plan_changes_base"
            return report

        if initial_sha == base_sha:
            report["branch"]["status"] = "at_base_commit"
            _set_evidence(report, "branch_content", "no_plan_commit_detected")
            effect = _base_plan_effect(client, plan.base_branch, plan.changes)
            _set_evidence(report, "base_plan_effect", effect)
            branch_matches_plan = False
        else:
            compare_endpoint = client._endpoint(
                "/compare/" + urllib.parse.quote(plan.base_branch, safe="/")
                + "..." + urllib.parse.quote(plan.branch_name, safe="/")
            )
            comparison = get(compare_endpoint)
            if not isinstance(comparison, dict):
                raise _InspectionStop("comparison_response_invalid")
            if comparison.get("status") != "ahead":
                raise _InspectionStop("branch_comparison_status_unexpected")
            files = comparison.get("files")
            if not isinstance(files, list):
                raise _InspectionStop("comparison_file_list_missing")
            if len(files) >= _COMPARE_FILE_LIMIT:
                raise _InspectionStop("comparison_may_be_truncated_at_file_limit")
            changed_paths: list[str] = []
            for item in files:
                if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                    raise _InspectionStop("comparison_file_entry_invalid")
                changed_paths.append(item["filename"])
            if len(changed_paths) != len(set(changed_paths)):
                raise _InspectionStop("comparison_contains_duplicate_paths")
            planned_paths = set(plan.changes)
            extras = sorted(set(changed_paths) - planned_paths)
            report["branch"]["changed_path_count"] = len(changed_paths)
            report["branch"]["unplanned_changed_paths"] = extras[:20]
            if extras:
                _set_evidence(report, "branch_diff", "unplanned_files_detected",
                              count=len(extras), paths=extras[:20])
                raise _InspectionStop("unplanned_changed_paths_detected")
            _set_evidence(report, "branch_diff", "within_plan_path_set",
                          changed_path_count=len(changed_paths))

            mismatched: list[str] = []
            for path, expected in plan.changes.items():
                actual = _read_remote_file(client, path, plan.branch_name, get)
                if actual != expected:
                    mismatched.append(path)
            if mismatched:
                report["branch"]["mismatched_paths"] = sorted(mismatched)[:20]
                _set_evidence(report, "planned_file_contents", "mismatch",
                              paths=sorted(mismatched)[:20], count=len(mismatched))
                raise _InspectionStop("planned_file_content_mismatch")
            _set_evidence(report, "planned_file_contents", "exact_match",
                          file_count=len(plan.changes))
            report["branch"]["status"] = "matches_plan"
            branch_matches_plan = True

        pulls = _list_matching_pull_requests(client, plan.branch_name, plan.base_branch)
        if len(pulls) > 1:
            report["pull_request"] = {"status": "multiple_matching", "matching_count": len(pulls),
                                      "items": pulls[:5]}
            raise _InspectionStop("multiple_matching_pull_requests")
        if not pulls:
            report["pull_request"] = {"status": "absent", "matching_count": 0}
            _set_evidence(report, "pull_request", "absent_in_complete_query")
        else:
            report["pull_request"] = {
                "status": pulls[0]["state"],
                "matching_count": 1,
                "items": pulls[:1],
            }
            _set_evidence(report, "pull_request", pulls[0]["state"], number=pulls[0]["number"])
            if pulls[0]["state"] == "closed":
                raise _InspectionStop("matching_pull_request_closed")
            if not branch_matches_plan:
                raise _InspectionStop("open_pull_request_branch_not_verified_as_matching_plan")

        # Check for an intervening ref change after all content, diff, and PR reads.
        final_ref = get(ref_endpoint)
        if (
            not isinstance(final_ref, dict)
            or not isinstance(final_ref.get("object"), dict)
            or final_ref["object"].get("sha") != initial_sha
        ):
            raise _InspectionStop("branch_ref_changed_during_inspection")
        _set_evidence(report, "branch_ref_final", "unchanged", head_sha=initial_sha)

        if initial_sha == base_sha and not pulls:
            if effect != "effective_change_confirmed":
                raise _InspectionStop(effect, "manual_review_required")
            report["status"] = "inspected"
            report["recommendation"] = "safe_to_retry_same_plan"
            report["reason_code"] = "branch_at_base_and_plan_changes_base"
        elif branch_matches_plan:
            report["status"] = "inspected"
            report["recommendation"] = "safe_to_retry_same_plan"
            report["reason_code"] = (
                "matching_branch_and_open_pull_request"
                if pulls else "matching_branch_without_pull_request"
            )
        else:
            raise _InspectionStop("publication_state_not_safe_to_retry")
        return report

    except _InspectionStop as stop:
        report["status"] = "blocked" if stop.recommendation == "blocked" else "review_required"
        report["recommendation"] = stop.recommendation
        report["reason_code"] = stop.code
        failure: dict[str, Any] = {"status": "blocked", "code": stop.code}
        if stop.http_status is not None:
            failure["http_status"] = stop.http_status
        report["failure"] = failure
        return report
    except (GitHubAPIError, GitHubOpsError, OSError, TimeoutError, ValueError, TypeError):
        # Do not expose raw transport/API messages or response bodies.
        report["status"] = "review_required"
        report["recommendation"] = "manual_review_required"
        report["reason_code"] = "unclassified_remote_inspection_error"
        report["failure"] = {"status": "blocked", "code": "unclassified_remote_inspection_error"}
        return report


def _read_plan(path: str) -> Any:
    raw = read_bounded_bytes(path, limit_bytes=_MAX_PLAN_BYTES, label="GitHub change plan")
    value = json.loads(raw.decode("utf-8"))
    enforce_json_depth(value, label="GitHub change plan")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect interrupted BotPyton GitHub publication; read-only, no writes."
    )
    parser.add_argument("--repository", required=True, metavar="OWNER/REPO",
                        help="repository to inspect; no repository is inferred from plan contents")
    parser.add_argument("--plan", required=True, metavar="PLAN_JSON",
                        help="existing declarative GitHub change-plan JSON file")
    args = parser.parse_args(argv)
    try:
        plan_data = _read_plan(args.plan)
        client = GitHubClient(repository=args.repository)
        result = inspect_github_publication(plan_data, client=client)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["recommendation"] == "safe_to_retry_same_plan" else 2
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, BoundedReadError, GitHubOpsError):
        # File/credential setup errors remain bounded and never print raw credentials.
        print(json.dumps({
            "schema_version": 1,
            "status": "blocked",
            "recommendation": "blocked",
            "reason_code": "plan_read_or_client_setup_failed",
            "writes_performed": False,
            "secrets_disclosed": False,
            "ai_enabled": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
