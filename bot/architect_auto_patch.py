"""Explicitly opt-in AI patch proposal and bounded repair loop.

The model produces untrusted text. BOTPYTON extracts one clearly labeled Git diff,
restricts it to explicitly selected source files, and verifies it in isolated worktrees.
It never changes the source working tree or pushes/merges a candidate.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from .architect import ArchitectPlanError, build_architecture_plan
from .architect_ai import FREE_MODEL, ArchitectAIError, _contains_secret_like_value, request_architecture_advice
from .local_changes import _reject_link_components
from .patch_cycle import PatchCycleError, apply_reviewed_patch
from .project_checks import ProjectCheckError, discover_project_checks
from .workspace import WorkspaceError, _git

MAX_ATTEMPTS = 2
MAX_PATCH_BYTES = 250_000
MAX_FEEDBACK_BYTES = 11_500
MAX_FEEDBACK_FIELD_CHARS = 3_500
MAX_TASK_ID_CHARS = 32
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_FENCE = chr(96) * 3
_DIFF_FENCE_RE = re.compile(
    re.escape(_FENCE) + r"[ \t]*(?:diff|patch)[ \t]*\r?\n(.*?)" + re.escape(_FENCE),
    re.IGNORECASE | re.DOTALL,
)
_PATCH_MARKER = "BORRADOR DE PARCHE (NO APLICADO)"


class ArchitectAutoPatchError(ValueError):
    """Raised when the opt-in patch loop request is invalid or unsafe."""


def extract_advisor_diff(advice: str) -> str | None:
    """Return exactly one marked, fenced unified diff; reject ambiguous model output."""
    if not isinstance(advice, str) or not advice.strip():
        return None
    marker = re.search(re.escape(_PATCH_MARKER), advice, flags=re.IGNORECASE)
    if marker is None:
        return None
    blocks = list(_DIFF_FENCE_RE.finditer(advice[marker.end():]))
    if len(blocks) != 1:
        return None
    patch = blocks[0].group(1).strip("\r\n ").replace("\r\n", "\n").replace("\r", "\n")
    raw = patch.encode("utf-8")
    if not patch.startswith("diff --git ") or not raw or len(raw) > MAX_PATCH_BYTES or b"\x00" in raw:
        return None
    return patch + "\n"


def _preflight_repository(root: Path, check_id: str) -> list[str]:
    top = _git(root, ["rev-parse", "--show-toplevel"])
    if top["exit_code"] != 0:
        raise ArchitectAutoPatchError("project_root must be the root of a Git repository")
    try:
        git_root = Path(top["stdout"].strip()).resolve()
    except (OSError, RuntimeError, ValueError):
        raise ArchitectAutoPatchError("Could not resolve the Git repository root") from None
    if git_root != root:
        raise ArchitectAutoPatchError(f"Select the repository root, not a subdirectory: {git_root}")
    status = _git(root, ["status", "--porcelain", "--untracked-files=all"])
    if status["exit_code"] != 0:
        raise ArchitectAutoPatchError("Could not verify the source repository status")
    if status.get("stdout", "").strip() or status.get("stdout_truncated"):
        raise ArchitectAutoPatchError(
            "Source repository is not clean. Commit or stash changes first; no AI request was sent."
        )
    try:
        discovered = discover_project_checks(root)
    except (ProjectCheckError, OSError, RuntimeError, ValueError) as exc:
        raise ArchitectAutoPatchError(
            f"Could not discover project checks ({type(exc).__name__}): {str(exc)[:400]}"
        ) from None
    available = [item["check_id"] for item in discovered["checks"]]
    if check_id not in available:
        raise ArchitectAutoPatchError(
            f"Requested check {check_id!r} was not discovered. Available checks: "
            + (", ".join(available) or "(none)")
        )
    return available


def _redact_root(value: object, root: Path) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    resolved = str(root)
    for variant in {resolved, resolved.replace("\\", "/"), resolved.replace("/", "\\")}:
        if variant:
            text = text.replace(variant, "[PROJECT_ROOT]")
    return text[:MAX_FEEDBACK_FIELD_CHARS]


def _feedback_from_failure(result: dict[str, Any], *, attempt: int, root: Path) -> dict[str, str]:
    candidate = result.get("candidate_check")
    candidate = candidate if isinstance(candidate, dict) else {}
    summary = {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "verification": result.get("verification"),
        "baseline_check": result.get("baseline_check"),
        "candidate_check": {
            key: candidate.get(key)
            for key in ("ok", "exit_code", "timed_out", "error")
            if key in candidate
        },
    }
    feedback = {
        "attempt": str(attempt),
        "previous_status": str(result.get("status", "unknown")),
        "failure_summary": _redact_root(summary, root),
        "diagnostic": _redact_root(result.get("diagnostic") or {}, root),
        "stdout_tail": _redact_root(candidate.get("stdout_tail", ""), root),
        "stderr_tail": _redact_root(candidate.get("stderr_tail", ""), root),
    }
    encoded = json.dumps(feedback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(encoded) > MAX_FEEDBACK_BYTES:
        largest = max(
            ("failure_summary", "diagnostic", "stdout_tail", "stderr_tail"),
            key=lambda field: len(feedback[field]),
        )
        if not feedback[largest]:
            break
        feedback[largest] = feedback[largest][:max(0, len(feedback[largest]) - 512)]
        encoded = json.dumps(feedback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if _contains_secret_like_value(encoded.decode("utf-8")):
        raise ArchitectAutoPatchError(
            "Failing output appears to contain a credential-like value. Repair feedback was not uploaded."
        )
    return feedback


def run_architect_auto_patch(
    brief: object,
    *,
    task_id: str,
    context_paths: list[str],
    project_check_id: str,
    allow_external_ai: bool,
    send_source_context: bool,
    execute_project_code: bool,
    send_repair_feedback: bool = False,
    max_attempts: int = 2,
    workspace_parent: str | Path | None = None,
    timeout_seconds: int = 120,
    memory_limit_mb: int | None = None,
    api_key: str | None = None,
    advisor: Callable[..., dict[str, Any]] | None = None,
    patch_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Request a constrained patch, test it once, and optionally ask for one revision."""
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise ArchitectAutoPatchError(
            f"task_id must be 1–{MAX_TASK_ID_CHARS} safe ASCII letters, digits, '.', '_' or '-'"
        )
    if not allow_external_ai:
        raise ArchitectAutoPatchError("Explicit external-AI consent is required before any advisor call")
    if not send_source_context or not isinstance(context_paths, list) or not context_paths:
        raise ArchitectAutoPatchError("Explicit source-context consent and selected source paths are required")
    if not execute_project_code:
        raise ArchitectAutoPatchError("The project check requires explicit --execute-project-code consent")
    if not isinstance(send_repair_feedback, bool):
        raise ArchitectAutoPatchError("send_repair_feedback must be boolean")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ArchitectAutoPatchError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}")
    if send_repair_feedback and max_attempts < 2:
        raise ArchitectAutoPatchError("--send-repair-feedback requires max_attempts=2")
    if len(context_paths) > 4 or not all(isinstance(path, str) and path for path in context_paths):
        raise ArchitectAutoPatchError("Select between 1 and 4 exact relative source paths")
    if len(set(context_paths)) != len(context_paths):
        raise ArchitectAutoPatchError("Selected source paths cannot be duplicated")
    if not isinstance(project_check_id, str) or not project_check_id.strip():
        raise ArchitectAutoPatchError("Select a discovered project check before asking BOTPYTON to repair code")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 300:
        raise ArchitectAutoPatchError("timeout_seconds must be between 1 and 300")
    if memory_limit_mb is not None and (
        not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool)
        or not 64 <= memory_limit_mb <= 1024
    ):
        raise ArchitectAutoPatchError("memory_limit_mb must be between 64 and 1024")

    try:
        local_plan = build_architecture_plan(brief)
    except ArchitectPlanError as exc:
        raise ArchitectAutoPatchError(f"Architecture brief rejected by local validation: {exc}") from exc
    if local_plan.get("decision") != "ready_to_plan":
        return {
            "status": "needs_information" if local_plan.get("decision") != "blocked" else "blocked",
            "local_plan": local_plan,
            "attempts": [],
            "network_used": False,
            "ai_enabled": False,
            "project_code_executed": False,
            "source_working_tree_modified": False,
            "automatic_merge": False,
            "automatic_push": False,
            "human_review_required": True,
            "note": "The local architecture kernel requires clarification or blocked the request; no API request was sent.",
        }

    root_value = brief.get("project_root") if isinstance(brief, dict) else None
    if not isinstance(root_value, str) or not root_value.strip():
        raise ArchitectAutoPatchError("The brief must include project_root")
    try:
        input_root = Path(root_value).expanduser()
        _reject_link_components(input_root, "Project root")
        root = input_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ArchitectAutoPatchError("project_root cannot be resolved safely; no API request was sent.") from None
    if not root.is_dir():
        raise ArchitectAutoPatchError("project_root must be a directory")
    try:
        available_checks = _preflight_repository(root, project_check_id)
    except (WorkspaceError, ProjectCheckError, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ArchitectAutoPatchError):
            raise
        raise ArchitectAutoPatchError(
            f"Preflight blocked the repair cycle ({type(exc).__name__}): {str(exc)[:500]}"
        ) from None

    advisor_call = advisor or request_architecture_advice
    patch_call = patch_runner or apply_reviewed_patch
    feedback: dict[str, str] | None = None
    seen_hashes: set[str] = set()
    attempts: list[dict[str, Any]] = []
    network_used = False
    project_code_executed = False
    project_code_execution_attempted = False

    for attempt in range(1, max_attempts + 1):
        try:
            advice_result = advisor_call(
                brief,
                api_key=api_key,
                context_paths=list(context_paths),
                send_source_context=True,
                repair_feedback=feedback,
            )
        except ArchitectAIError as exc:
            return {
                "status": "advisor_error",
                "error": str(exc),
                "provider": "OpenRouter",
                "model": FREE_MODEL,
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used or exc.network_used,
                "ai_enabled": network_used or exc.network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "No automatic network retry, source modification or merge was attempted.",
            }
        network_used = network_used or bool(advice_result.get("safety", {}).get("network_used"))
        if advice_result.get("status") != "advice_received":
            advise_status = advice_result.get("status", "advisor_error")
            return {
                "status": "blocked" if advise_status == "blocked" else "needs_information",
                "advisor_result_status": advise_status,
                "local_plan": advice_result.get("local_plan", local_plan),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The advisor did not provide an actionable response; no patch was applied.",
            }

        advice = advice_result.get("advice", "")
        patch_text = extract_advisor_diff(advice)
        if patch_text is None:
            return {
                "status": "needs_information",
                "provider": advice_result.get("provider", "OpenRouter"),
                "model": advice_result.get("model", FREE_MODEL),
                "advisor_advice_excerpt": str(advice)[:6_000],
                "local_plan": advice_result.get("local_plan", local_plan),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "No unambiguous unified diff was found under BORRADOR DE PARCHE (NO APLICADO). Nothing was applied.",
            }
        patch_sha = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        if patch_sha in seen_hashes:
            return {
                "status": "stopped_repeated_patch",
                "attempts": attempts,
                "attempt_count": len(attempts),
                "repeated_patch_sha256": patch_sha,
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The adviser repeated an earlier patch; the loop stopped.",
            }
        seen_hashes.add(patch_sha)

        patch_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="botpyton-ai-candidate-", suffix=".patch", delete=False) as stream:
                patch_file = stream.name
                stream.write(patch_text.encode("utf-8"))
            candidate = patch_call(
                patch_file,
                root,
                f"{task_id}-a{attempt}",
                workspace_parent=workspace_parent,
                project_check_id=project_check_id,
                execute_project_code=True,
                allowed_paths=list(context_paths),
                timeout_seconds=timeout_seconds,
                memory_limit_mb=memory_limit_mb,
            )
        except PatchCycleError as exc:
            return {
                "status": "patch_blocked",
                "error": str(exc)[:1_000],
                "patch_sha256": patch_sha,
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The local patch policy rejected the proposal. Policy rejection is not retried automatically.",
            }
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "status": "patch_cycle_error",
                "error": f"{type(exc).__name__}: {str(exc)[:800]}",
                "patch_sha256": patch_sha,
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The local cycle stopped without an automatic retry or merge.",
            }
        finally:
            if patch_file:
                try:
                    Path(patch_file).unlink()
                except OSError:
                    pass

        project_code_executed = project_code_executed or bool(candidate.get("project_code_executed"))
        project_code_execution_attempted = project_code_execution_attempted or bool(candidate.get("project_code_execution_attempted"))
        entry = {
            "attempt": attempt,
            "patch_sha256": patch_sha,
            "files": candidate.get("patch", {}).get("files", []),
            "candidate_status": candidate.get("status"),
            "candidate_workspace": candidate.get("candidate_workspace"),
            "candidate_branch": candidate.get("candidate_branch"),
            "baseline_check": candidate.get("baseline_check"),
            "candidate_check": candidate.get("candidate_check"),
            "verification": candidate.get("verification"),
            "diagnostic": candidate.get("diagnostic"),
            "reason": candidate.get("reason"),
            "candidate_report": candidate,
            "repair_feedback_uploaded_on_next_attempt": feedback is not None,
        }
        attempts.append(entry)

        if candidate.get("status") == "candidate_tests_passed":
            return {
                "status": "candidate_tests_passed",
                "decision": "review_candidate",
                "provider": advice_result.get("provider", "OpenRouter"),
                "model": advice_result.get("model", FREE_MODEL),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "max_attempts": max_attempts,
                "available_checks": available_checks,
                "selected_source_paths": list(context_paths),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "Candidate checks passed in an isolated worktree. Human review is required; source files, remote branches and main were not changed.",
            }

        if candidate.get("status") == "candidate_ready_for_review":
            return {
                "status": "candidate_ready_for_review",
                "decision": "review_candidate",
                "attempts": attempts,
                "attempt_count": len(attempts),
                "available_checks": available_checks,
                "selected_source_paths": list(context_paths),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "Only static checks passed. No behavioral success is claimed.",
            }

        retryable = candidate.get("status") in {
            "candidate_checks_failed",
            "candidate_static_verification_failed",
            "patch_rejected",
        }
        if not retryable:
            return {
                "status": candidate.get("status", "patch_cycle_error"),
                "decision": "ask_human_to_review",
                "attempts": attempts,
                "attempt_count": len(attempts),
                "max_attempts": max_attempts,
                "available_checks": available_checks,
                "selected_source_paths": list(context_paths),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "A non-retryable local verification state occurred; the loop stopped for human review.",
            }

        if attempt >= max_attempts:
            return {
                "status": "repair_attempt_limit_reached",
                "decision": "ask_human_to_review",
                "last_candidate_status": candidate.get("status"),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "max_attempts": max_attempts,
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The hard attempt limit was reached; no further patch request was sent.",
            }

        if not send_repair_feedback:
            return {
                "status": candidate.get("status"),
                "decision": "ask_human_to_review",
                "last_candidate_status": candidate.get("status"),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "max_attempts": max_attempts,
                "repair_retry_available": False,
                "next_action": "A second model call requires the separate --send-repair-feedback consent flag.",
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "The first candidate failed verification. Feedback stayed local; no second request was sent.",
            }

        try:
            feedback = _feedback_from_failure(candidate, attempt=attempt, root=root)
        except ArchitectAutoPatchError as exc:
            return {
                "status": "repair_feedback_blocked",
                "error": str(exc),
                "attempts": attempts,
                "attempt_count": len(attempts),
                "network_used": network_used,
                "ai_enabled": network_used,
                "project_code_executed": project_code_executed,
                "project_code_execution_attempted": project_code_execution_attempted,
                "source_working_tree_modified": False,
                "automatic_merge": False,
                "automatic_push": False,
                "human_review_required": True,
                "note": "Diagnostic output remained local because it could not be screened safely for upload.",
            }

    return {
        "status": "repair_attempt_limit_reached",
        "decision": "ask_human_to_review",
        "attempts": attempts,
        "attempt_count": len(attempts),
        "max_attempts": max_attempts,
        "network_used": network_used,
        "ai_enabled": network_used,
        "project_code_executed": project_code_executed,
        "project_code_execution_attempted": project_code_execution_attempted,
        "source_working_tree_modified": False,
        "automatic_merge": False,
        "automatic_push": False,
        "human_review_required": True,
        "note": "Repair loop stopped at its hard attempt limit.",
    }
