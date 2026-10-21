"""Read-only readiness checks for explicitly requested GitHub repository operations."""
from __future__ import annotations

import urllib.parse
from typing import Any

from .github_ops import GitHubAPIError, GitHubClient, GitHubOpsError

_BRANCH_REJECTED = ("..", "//", "@{", "\\")
_WRITE_PERMISSIONS = (
    {"permission": "Contents", "access": "write"},
    {"permission": "Pull requests", "access": "write"},
)


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, GitHubAPIError):
        if exc.status == 401:
            return "authentication_invalid_or_expired"
        if exc.status == 403:
            return "permission_or_policy_blocked"
        if exc.status == 404:
            return "missing_or_not_accessible"
        if exc.status == 429:
            return "rate_limited"
        if exc.status >= 500:
            return "github_service_unavailable"
        return "api_request_rejected"
    if "GITHUB_TOKEN is required" in str(exc):
        return "credentials_missing"
    return "transport_error"


def _public_failure(exc: BaseException) -> dict[str, Any]:
    result = {"ok": False, "code": _failure_code(exc)}
    if isinstance(exc, GitHubAPIError):
        result["http_status"] = exc.status
    return result


def _validate_branch(branch: Any) -> str:
    if not isinstance(branch, str) or not branch or len(branch) > 240:
        raise GitHubOpsError("GitHub branch must be a non-empty name no longer than 240 characters")
    if (
        branch.startswith("/")
        or branch.endswith("/")
        or branch.endswith(".")
        or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in branch)
        or any(token in branch for token in _BRANCH_REJECTED)
    ):
        raise GitHubOpsError("GitHub branch name is not a safe canonical ref")
    if any(part in {"", ".", ".."} or part.endswith(".lock") for part in branch.split("/")):
        raise GitHubOpsError("GitHub branch name contains an invalid ref component")
    return branch


def run_github_preflight(
    repository: str | None = None,
    *,
    branch: str | None = None,
    intent: str = "inspect",
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Check authenticated read capabilities without performing any GitHub mutation."""
    if intent not in {"inspect", "draft-pr"}:
        raise GitHubOpsError("GitHub preflight intent must be 'inspect' or 'draft-pr'")
    active = client or GitHubClient(repository=repository)
    if repository is not None and active.repository != repository:
        raise GitHubOpsError("Injected GitHub client repository does not match the requested repository")
    checks: dict[str, Any] = {
        "repository_metadata": {"ok": False, "status": "not_run"},
        "target_branch": {"ok": False, "status": "not_run"},
        "pull_requests_read": {"ok": False, "status": "not_run"},
        "actions_runs_read": {"ok": False, "status": "not_run"},
    }
    authenticated = False
    repository_info: dict[str, Any] | None = None

    try:
        repository_info = active.request("GET", active._endpoint(""))
        if not isinstance(repository_info, dict):
            raise GitHubOpsError("GitHub returned invalid repository metadata")
        default_branch = repository_info.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubOpsError("GitHub repository metadata lacks a default branch")
        authenticated = bool(active.token)
        checks["repository_metadata"] = {
            "ok": True,
            "status": "passed",
            "repository": str(repository_info.get("full_name") or active.repository),
            "private": repository_info.get("private") if isinstance(repository_info.get("private"), bool) else None,
            "default_branch": default_branch,
        }
        repo_role = repository_info.get("permissions")
        if isinstance(repo_role, dict):
            checks["repository_metadata"]["repository_role"] = {
                key: repo_role[key]
                for key in ("pull", "push", "admin")
                if isinstance(repo_role.get(key), bool)
            }
    except (GitHubAPIError, GitHubOpsError, OSError, ValueError) as exc:
        checks["repository_metadata"] = {"status": "blocked", **_public_failure(exc)}
        code = checks["repository_metadata"].get("code")
        if code in {
            "credentials_missing", "authentication_invalid_or_expired",
            "permission_or_policy_blocked", "missing_or_not_accessible",
        }:
            for key in ("target_branch", "pull_requests_read", "actions_runs_read"):
                checks[key] = {"ok": False, "status": "not_run", "reason": "repository access was not established"}
            return _compose_result(active.repository, intent, authenticated, checks, repository_info)
        for key in ("target_branch", "pull_requests_read", "actions_runs_read"):
            checks[key] = {"ok": False, "status": "not_run", "reason": "repository metadata check failed"}
        return _compose_result(active.repository, intent, authenticated, checks, repository_info)

    selected_branch = _validate_branch(branch if branch is not None else repository_info["default_branch"])
    ref_endpoint = active._endpoint(
        "/git/ref/heads/" + urllib.parse.quote(selected_branch, safe="/")
    )
    try:
        ref_info = active.request("GET", ref_endpoint)
        object_info = ref_info.get("object") if isinstance(ref_info, dict) else None
        if not isinstance(object_info, dict) or not isinstance(object_info.get("sha"), str):
            raise GitHubOpsError("GitHub returned an invalid branch ref")
        checks["target_branch"] = {
            "ok": True,
            "status": "passed",
            "branch": selected_branch,
            "head_sha": object_info["sha"],
        }
    except (GitHubAPIError, GitHubOpsError, OSError, ValueError) as exc:
        checks["target_branch"] = {"status": "blocked", "branch": selected_branch, **_public_failure(exc)}

    for key, suffix, expected_key in (
        ("pull_requests_read", "/pulls?state=open&per_page=1", None),
        ("actions_runs_read", "/actions/runs?per_page=1", "workflow_runs"),
    ):
        try:
            response = active.request("GET", active._endpoint(suffix))
            if key == "pull_requests_read":
                valid = isinstance(response, list)
            else:
                valid = isinstance(response, dict) and isinstance(response.get(expected_key), list)
            if not valid:
                raise GitHubOpsError("GitHub returned an unexpected response shape")
            details: dict[str, Any] = {"ok": True, "status": "passed"}
            if key == "pull_requests_read":
                details["open_pull_requests_sampled"] = min(len(response), 1)
            else:
                details["workflow_runs_sampled"] = min(len(response[expected_key]), 1)
            checks[key] = details
        except (GitHubAPIError, GitHubOpsError, OSError, ValueError) as exc:
            checks[key] = {"status": "blocked", **_public_failure(exc)}

    return _compose_result(active.repository, intent, authenticated, checks, repository_info)


def _compose_result(
    repository: str,
    intent: str,
    authenticated: bool,
    checks: dict[str, Any],
    repository_info: dict[str, Any] | None,
) -> dict[str, Any]:
    all_passed = all(item.get("ok") is True for item in checks.values())
    if all_passed:
        status = "ready"
    elif any(item.get("ok") is True for item in checks.values()):
        status = "partial"
    else:
        status = "blocked"

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "repository": repository,
        "intent": intent,
        "authenticated_credential_used": authenticated,
        "checks": checks,
        "network_used": True,
        "writes_performed": False,
        "secrets_disclosed": False,
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }
    if intent == "draft-pr":
        role = repository_info.get("permissions") if isinstance(repository_info, dict) else None
        repo_push = role.get("push") if isinstance(role, dict) and isinstance(role.get("push"), bool) else None
        result["future_write_capability"] = {
            "status": "not_verified_by_read_only_probe",
            "repository_role_push": repo_push,
            "required_fine_grained_token_permissions": [dict(item) for item in _WRITE_PERMISSIONS],
            "note": "Successful read checks do not prove token write scopes, branch-policy acceptance, SSO authorization, or permission to create a draft pull request. No write request was attempted.",
        }
    return result
