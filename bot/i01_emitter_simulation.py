"""Simulation-only Design A emitter boundary prototype.

This module deliberately has no GitHub client, network transport, token handling,
subprocess execution, or filesystem access. It records an authorized intent only.
It is not a production trust boundary and must never be wired to a live sender.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


REQUEST_FIELDS = frozenset({
    "schema_version", "request_id", "task_id", "repository", "base_ref",
    "head_ref", "head_sha", "title", "body", "draft",
})
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class EmitterRequestError(ValueError):
    """Raised when a request fails the independent simulation policy."""


@dataclass(frozen=True)
class EmitterPolicy:
    """Trusted policy supplied by the emitter deployment, never by the request."""

    repository: str
    authorized_actors: frozenset[str]
    allowed_base_refs: frozenset[str]
    allowed_head_prefixes: tuple[str, ...]
    max_title_chars: int = 200
    max_body_chars: int = 20_000

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ValueError("policy repository must use owner/repository form")
        if not self.authorized_actors or any(not isinstance(x, str) or not x.strip() for x in self.authorized_actors):
            raise ValueError("policy must define at least one non-empty authorized actor")
        if not self.allowed_base_refs or any(not _valid_ref(x) for x in self.allowed_base_refs):
            raise ValueError("policy must define valid allowed base refs")
        if not self.allowed_head_prefixes or any(
            not isinstance(x, str) or not x.endswith("/") or not _valid_ref(x[:-1])
            for x in self.allowed_head_prefixes
        ):
            raise ValueError("policy must define allowed head-ref prefixes ending in '/'")
        if not 1 <= self.max_title_chars <= 200 or not 0 <= self.max_body_chars <= 20_000:
            raise ValueError("policy text limits exceed hard maximums")


def _valid_ref(value: Any) -> bool:
    if not isinstance(value, str) or not REF_RE.fullmatch(value):
        return False
    if value.startswith("/") or value.endswith(("/", ".")) or "//" in value or ".." in value or "@{" in value:
        return False
    parts = value.split("/")
    if any(not part or part in {".", ".."} or part.endswith(".lock") for part in parts):
        return False
    return True


def validate_emitter_request(
    request: Any,
    *,
    authenticated_actor: str,
    resolved_head_sha: str,
    policy: EmitterPolicy,
) -> dict[str, Any]:
    """Validate request data against independently supplied trusted facts.

    authenticated_actor and resolved_head_sha must come from the trusted caller,
    not from fields in request. This prototype does not fetch either value.
    """
    if authenticated_actor not in policy.authorized_actors:
        raise EmitterRequestError("requesting identity is not authorized")
    if not isinstance(request, dict):
        raise EmitterRequestError("request must be a JSON object")
    if set(request) != REQUEST_FIELDS:
        missing = sorted(REQUEST_FIELDS - set(request))
        unexpected = sorted(set(request) - REQUEST_FIELDS)
        raise EmitterRequestError(
            f"request fields mismatch (missing={missing}, unexpected={unexpected})"
        )
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise EmitterRequestError("unsupported schema_version")
    if not isinstance(request["request_id"], str) or not REQUEST_ID_RE.fullmatch(request["request_id"]):
        raise EmitterRequestError("request_id is invalid")
    if not isinstance(request["task_id"], str) or not TASK_ID_RE.fullmatch(request["task_id"]):
        raise EmitterRequestError("task_id is invalid")
    if request["repository"] != policy.repository:
        raise EmitterRequestError("repository is not authorized")
    base_ref = request["base_ref"]
    if not _valid_ref(base_ref) or base_ref not in policy.allowed_base_refs:
        raise EmitterRequestError("base_ref is not authorized")
    head_ref = request["head_ref"]
    if not _valid_ref(head_ref) or not any(head_ref.startswith(p) for p in policy.allowed_head_prefixes):
        raise EmitterRequestError("head_ref is not authorized")
    if head_ref == base_ref:
        raise EmitterRequestError("head_ref cannot equal base_ref")
    requested_sha = request["head_sha"]
    if not isinstance(requested_sha, str) or not SHA_RE.fullmatch(requested_sha):
        raise EmitterRequestError("head_sha must be a full lowercase 40-character SHA")
    if not isinstance(resolved_head_sha, str) or not SHA_RE.fullmatch(resolved_head_sha):
        raise EmitterRequestError("trusted resolved SHA is missing or invalid")
    if requested_sha != resolved_head_sha:
        raise EmitterRequestError("head_sha does not match independently resolved commit")
    title = request["title"]
    body = request["body"]
    if not isinstance(title, str) or not title.strip() or len(title) > policy.max_title_chars:
        raise EmitterRequestError("title is empty or exceeds policy limit")
    if not isinstance(body, str) or len(body) > policy.max_body_chars:
        raise EmitterRequestError("body is invalid or exceeds policy limit")
    if type(request["draft"]) is not bool or request["draft"] is not True:
        raise EmitterRequestError("only draft pull requests are allowed")
    return {
        "schema_version": 1,
        "request_id": request["request_id"],
        "task_id": request["task_id"],
        "repository": policy.repository,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "head_sha": requested_sha,
        "title": title.strip(),
        "body": body,
        "draft": True,
    }


class SimulationRecorder:
    """In-memory sink for simulated intents; intentionally has no API/write method."""

    def __init__(self) -> None:
        self.operations: list[dict[str, Any]] = []

    def record(self, validated_request: dict[str, Any]) -> dict[str, Any]:
        operation = {
            "mode": "simulation_only",
            "operation": "would_create_draft_pull_request",
            "request_id": validated_request["request_id"],
            "repository": validated_request["repository"],
            "base_ref": validated_request["base_ref"],
            "head_ref": validated_request["head_ref"],
            "head_sha": validated_request["head_sha"],
            "draft": True,
            "live_write_performed": False,
        }
        self.operations.append(operation)
        return dict(operation)


class SimulationOnlyEmitter:
    """Fail-closed request gate that can only append to an in-memory recorder."""

    def __init__(self, policy: EmitterPolicy, recorder: SimulationRecorder | None = None) -> None:
        self.policy = policy
        self.recorder = recorder if recorder is not None else SimulationRecorder()
        self._seen_request_ids: set[str] = set()

    def process(
        self,
        request: Any,
        *,
        authenticated_actor: str,
        resolved_head_sha: str,
    ) -> dict[str, Any]:
        validated = validate_emitter_request(
            request,
            authenticated_actor=authenticated_actor,
            resolved_head_sha=resolved_head_sha,
            policy=self.policy,
        )
        if validated["request_id"] in self._seen_request_ids:
            raise EmitterRequestError("request_id has already been processed")
        result = self.recorder.record(validated)
        self._seen_request_ids.add(validated["request_id"])
        return result
