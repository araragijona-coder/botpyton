"""Least-privilege GitHub branch/commit/draft-PR operations (stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .policy import validate_relative_path

MAX_FILES_PER_PLAN = 20
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 500_000
ALLOWED_SUFFIXES = frozenset({".py", ".md", ".json", ".toml", ".txt"})
PROTECTED_EXACT = frozenset({".cerebro/config.json"})
PROTECTED_PREFIXES = (
    ".github/",
    ".cerebro/policies/",
    ".cerebro/memory/",
    ".cerebro/reports/",
    ".cerebro/backups/",
    ".git/",
    ".venv/",
    "node_modules/",
    "dist/",
    "build/",
)
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


_SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE_KEY")


def _github_cli_token() -> str:
    """Read an existing GitHub CLI credential without printing or persisting it."""
    executable = shutil.which("gh")
    if not executable:
        return ""
    env = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SECRET_ENV_MARKERS)
    }
    try:
        result = subprocess.run(
            [executable, "auth", "token", "--hostname", "github.com"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            shell=False,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    token = result.stdout.strip()
    if result.returncode != 0 or not 20 <= len(token) <= 512 or not re.fullmatch(r"[A-Za-z0-9_]+", token):
        return ""
    return token


class GitHubOpsError(ValueError):
    """Raised for a rejected plan, API failure, or non-idempotent GitHub state."""


class GitHubAPIError(GitHubOpsError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API returned HTTP {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class NormalizedPlan:
    task_id: str
    base_branch: str
    title: str
    body: str
    commit_message: str
    draft: bool
    changes: dict[str, str]
    fingerprint: str
    branch_name: str


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _validate_change_path(raw_path: Any) -> str:
    path = validate_relative_path(raw_path)
    if path != raw_path or path in {"", "."}:
        raise GitHubOpsError(f"Path is not canonical: {raw_path!r}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise GitHubOpsError("Paths cannot contain control characters")
    if path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        raise GitHubOpsError(f"Protected path cannot be included in a bot-generated change: {path}")
    if not any(path.lower().endswith(suffix) for suffix in ALLOWED_SUFFIXES):
        raise GitHubOpsError(
            f"Unsupported file type for automatic GitHub changes: {path}; "
            f"allowed suffixes: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )
    return path


def validate_change_plan(plan: Any) -> NormalizedPlan:
    """Validate a declarative file-change plan before touching the GitHub API."""
    if not isinstance(plan, dict):
        raise GitHubOpsError("Change plan must be a JSON object")
    if plan.get("schema_version") != 1:
        raise GitHubOpsError("Unsupported GitHub change-plan schema_version")
    task_id = plan.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise GitHubOpsError("task_id must contain 1–64 safe ASCII letters, digits, '.', '_' or '-'")
    base_branch = plan.get("base_branch", "main")
    if not isinstance(base_branch, str) or not base_branch.strip():
        raise GitHubOpsError("base_branch must be a non-empty branch name")
    if base_branch.startswith("botpyton/") or ".." in base_branch or any(ch in base_branch for ch in "\r\n"):
        raise GitHubOpsError("base_branch cannot be a botpyton task branch or malformed ref")
    title = plan.get("title")
    body = plan.get("body", "")
    commit_message = plan.get("commit_message", title)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise GitHubOpsError("title must be 1–200 characters")
    if not isinstance(body, str) or len(body) > 20_000:
        raise GitHubOpsError("body must be a string of at most 20000 characters")
    if not isinstance(commit_message, str) or not commit_message.strip() or len(commit_message) > 200:
        raise GitHubOpsError("commit_message must be 1–200 characters")
    draft = plan.get("draft", True)
    if not isinstance(draft, bool):
        raise GitHubOpsError("draft must be a JSON boolean")
    if draft is not True:
        raise GitHubOpsError("draft must be true; non-draft pull requests are disabled")
    raw_changes = plan.get("changes")
    if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= MAX_FILES_PER_PLAN:
        raise GitHubOpsError(f"changes must be a list containing 1–{MAX_FILES_PER_PLAN} files")

    changes: dict[str, str] = {}
    total_bytes = 0
    for item in raw_changes:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise GitHubOpsError("Each change must contain exactly 'path' and 'content'")
        path = _validate_change_path(item["path"])
        content = item["content"]
        if not isinstance(content, str):
            raise GitHubOpsError(f"File content must be text: {path}")
        if path in changes:
            raise GitHubOpsError(f"Duplicate path in change plan: {path}")
        raw_size = len(content.encode("utf-8"))
        if raw_size > MAX_FILE_BYTES:
            raise GitHubOpsError(f"File exceeds {MAX_FILE_BYTES} bytes: {path}")
        total_bytes += raw_size
        changes[path] = content
    if total_bytes > MAX_TOTAL_BYTES:
        raise GitHubOpsError(f"Plan exceeds {MAX_TOTAL_BYTES} bytes of total new content")

    canonical = {
        "schema_version": 1,
        "task_id": task_id,
        "base_branch": base_branch,
        "title": title.strip(),
        "body": body,
        "commit_message": commit_message.strip(),
        "draft": draft,
        "changes": [{"path": path, "content": changes[path]} for path in sorted(changes)],
    }
    fingerprint = hashlib.sha256(_canonical(canonical)).hexdigest()
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:24] or "task"
    branch_name = f"botpyton/{slug}-{fingerprint[:12]}"
    if len(branch_name) > 240:
        raise GitHubOpsError("Generated branch name exceeds GitHub limits")
    return NormalizedPlan(
        task_id=task_id,
        base_branch=base_branch,
        title=title.strip(),
        body=body,
        commit_message=commit_message.strip(),
        draft=draft,
        changes=changes,
        fingerprint=fingerprint,
        branch_name=branch_name,
    )


class GitHubClient:
    """GitHub REST client; API host is fixed and token is never logged."""

    def __init__(
        self,
        repository: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: int = 20,
        transport: Callable[[str, str, dict[str, Any] | None], Any] | None = None,
    ):
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY", "")
        if not REPO_RE.fullmatch(self.repository):
            raise GitHubOpsError("GITHUB_REPOSITORY must have owner/repository form")
        self.owner, self.repo = self.repository.split("/", 1)
        environment_token = os.environ.get("GITHUB_TOKEN", "")
        self.token = token if token is not None else environment_token
        self._allow_github_cli = token is None and not environment_token
        self.timeout_seconds = max(1, min(int(timeout_seconds), 60))
        self.transport = transport

    def request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        if not endpoint.startswith("/repos/") or endpoint.startswith("//") or "://" in endpoint:
            raise GitHubOpsError("Only repository-scoped GitHub API endpoints are permitted")
        if self.transport is not None:
            return self.transport(method, endpoint, payload)
        if not self.token and self._allow_github_cli:
            self.token = _github_cli_token()
        if not self.token:
            raise GitHubOpsError("GITHUB_TOKEN or an authenticated GitHub CLI session is required for real GitHub operations")
        url = "https://api.github.com" + endpoint
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "BotPyton-Deterministic-Worker",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                response_body = exc.read().decode("utf-8", errors="replace")
                decoded = json.loads(response_body) if response_body else {}
                message = decoded.get("message", "request rejected") if isinstance(decoded, dict) else "request rejected"
            except (ValueError, UnicodeError):
                message = "request rejected"
            raise GitHubAPIError(exc.code, str(message)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubOpsError(f"GitHub API transport failed: {type(exc).__name__}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GitHubOpsError("GitHub API returned invalid JSON") from exc

    def _endpoint(self, suffix: str) -> str:
        repo_path = urllib.parse.quote(self.repository, safe="/")
        return f"/repos/{repo_path}{suffix}"


def _ref_endpoint(client: GitHubClient, branch: str) -> str:
    return client._endpoint("/git/ref/heads/" + urllib.parse.quote(branch, safe="/"))


def _get_ref(client: GitHubClient, branch: str) -> dict[str, Any] | None:
    try:
        result = client.request("GET", _ref_endpoint(client, branch))
    except GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise
    obj = result.get("object", {})
    if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
        raise GitHubOpsError(f"GitHub returned an invalid ref for {branch}")
    return result


def _read_file(client: GitHubClient, path: str, branch: str) -> str | None:
    endpoint = client._endpoint("/contents/" + urllib.parse.quote(path, safe="/"))
    endpoint += "?" + urllib.parse.urlencode({"ref": branch})
    try:
        result = client.request("GET", endpoint)
    except GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(result, dict) or result.get("type") != "file":
        raise GitHubOpsError(f"Existing GitHub path is not a regular file: {path}")
    if result.get("encoding") != "base64" or not isinstance(result.get("content"), str):
        raise GitHubOpsError(f"GitHub did not return base64 file content for {path}")
    try:
        return base64.b64decode(result["content"], validate=False).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise GitHubOpsError(f"Cannot decode existing repository file: {path}") from exc


def _existing_changes_match(
    client: GitHubClient, plan: NormalizedPlan, branch: str, base_branch: str
) -> bool:
    for path, expected in plan.changes.items():
        if _read_file(client, path, branch) != expected:
            return False
    # Also inspect the branch diff so a human-added extra file cannot sneak into
    # the same pull request just because the declared paths still match.
    endpoint = client._endpoint(
        "/compare/" + urllib.parse.quote(base_branch, safe="/")
        + "..." + urllib.parse.quote(branch, safe="/")
    )
    comparison = client.request("GET", endpoint)
    if not isinstance(comparison, dict):
        return False
    files = comparison.get("files")
    if not isinstance(files, list) or len(files) > MAX_FILES_PER_PLAN:
        return False
    if comparison.get("status") not in {"ahead", "identical"}:
        return False
    changed_paths = {
        item.get("filename") for item in files
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    if len(changed_paths) != len(files):
        return False
    return changed_paths.issubset(set(plan.changes))


def _find_existing_pull_request(client: GitHubClient, plan: NormalizedPlan) -> dict[str, Any] | None:
    head = f"{client.owner}:{plan.branch_name}"
    endpoint = client._endpoint("/pulls") + "?" + urllib.parse.urlencode({
        "state": "all", "head": head, "base": plan.base_branch, "per_page": 100
    })
    records = client.request("GET", endpoint)
    if not isinstance(records, list):
        raise GitHubOpsError("GitHub returned an invalid pull-request list")
    matching = [
        item for item in records
        if isinstance(item, dict) and item.get("head", {}).get("ref") == plan.branch_name
        and item.get("base", {}).get("ref") == plan.base_branch
    ]
    if not matching:
        return None
    matching.sort(key=lambda item: int(item.get("number", 0)), reverse=True)
    pull = matching[0]
    if pull.get("state") != "open":
        raise GitHubOpsError(
            f"A prior pull request for this deterministic branch is {pull.get('state')}; "
            "refusing to create a duplicate pull request"
        )
    return pull


def submit_change_plan(
    plan_data: Any,
    *,
    client: GitHubClient,
    execute: bool = False,
) -> dict[str, Any]:
    """Dry-run by default; on execute create a safe branch, commit and draft PR."""
    plan = validate_change_plan(plan_data)
    if not execute:
        return {
            "mode": "dry_run",
            "task_id": plan.task_id,
            "fingerprint": plan.fingerprint,
            "repository": client.repository,
            "base_branch": plan.base_branch,
            "branch_name": plan.branch_name,
            "title": plan.title,
            "draft": plan.draft,
            "files": sorted(plan.changes),
            "total_bytes": sum(len(v.encode("utf-8")) for v in plan.changes.values()),
            "operations": ["create_branch_if_absent", "create_one_commit", "open_pull_request_if_absent"],
            "automatic_merge": False,
            "ai_enabled": False,
            "external_ai_apis_enabled": False,
        }

    repository_info = client.request("GET", client._endpoint(""))
    default_branch = repository_info.get("default_branch") if isinstance(repository_info, dict) else None
    if not isinstance(default_branch, str) or not default_branch:
        raise GitHubOpsError("Cannot determine repository default branch")
    if plan.base_branch != default_branch:
        raise GitHubOpsError(
            f"Plan base branch must equal repository default branch {default_branch!r}"
        )
    base_ref = _get_ref(client, plan.base_branch)
    if base_ref is None:
        raise GitHubOpsError(f"Base branch does not exist: {plan.base_branch}")
    base_sha = base_ref["object"]["sha"]

    branch_ref = _get_ref(client, plan.branch_name)
    if branch_ref is None:
        client.request("POST", client._endpoint("/git/refs"), {
            "ref": f"refs/heads/{plan.branch_name}", "sha": base_sha
        })
        branch_sha = base_sha
        branch_created = True
    else:
        branch_sha = branch_ref["object"]["sha"]
        branch_created = False
        if branch_sha != base_sha:
            if not _existing_changes_match(client, plan, plan.branch_name, plan.base_branch):
                raise GitHubOpsError(
                    "The deterministic branch already contains a different change; "
                    "refusing to overwrite or extend it"
                )

    if branch_sha == base_sha:
        base_commit = client.request(
            "GET", client._endpoint("/git/commits/" + urllib.parse.quote(base_sha, safe=""))
        )
        base_tree = base_commit.get("tree", {}).get("sha")
        if not isinstance(base_tree, str) or not base_tree:
            raise GitHubOpsError("GitHub returned an invalid base tree")
        entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in sorted(plan.changes.items())
        ]
        tree = client.request("POST", client._endpoint("/git/trees"), {
            "base_tree": base_tree, "tree": entries
        })
        tree_sha = tree.get("sha")
        if not isinstance(tree_sha, str) or not tree_sha:
            raise GitHubOpsError("GitHub returned an invalid tree SHA")
        commit = client.request("POST", client._endpoint("/git/commits"), {
            "message": plan.commit_message,
            "tree": tree_sha,
            "parents": [base_sha],
        })
        commit_sha = commit.get("sha")
        if not isinstance(commit_sha, str) or not commit_sha:
            raise GitHubOpsError("GitHub returned an invalid commit SHA")
        client.request("PATCH", _ref_endpoint(client, plan.branch_name), {
            "sha": commit_sha, "force": False
        })
        branch_sha = commit_sha
        commit_created = True
    else:
        commit_sha = branch_sha
        commit_created = False

    pull = _find_existing_pull_request(client, plan)
    pull_created = False
    if pull is None:
        body = plan.body.rstrip()
        marker = f"\n\n---\nBotPyton plan fingerprint: {plan.fingerprint}\nAutomatic merge: disabled."
        pull_body = (body + marker).strip()
        pull = client.request("POST", client._endpoint("/pulls"), {
            "title": plan.title,
            "head": plan.branch_name,
            "base": plan.base_branch,
            "body": pull_body,
            "draft": plan.draft,
        })
        pull_created = True
    if not isinstance(pull, dict) or not isinstance(pull.get("html_url"), str):
        raise GitHubOpsError("GitHub returned an invalid pull-request response")
    return {
        "mode": "executed",
        "repository": client.repository,
        "task_id": plan.task_id,
        "fingerprint": plan.fingerprint,
        "branch_name": plan.branch_name,
        "branch_created": branch_created,
        "commit_sha": commit_sha,
        "commit_created": commit_created,
        "pull_request_number": pull.get("number"),
        "pull_request_url": pull["html_url"],
        "pull_request_created": pull_created,
        "draft": bool(pull.get("draft", plan.draft)),
        "automatic_merge": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
    }
