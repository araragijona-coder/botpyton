"""Portable integrity-checked JSON checkpoint for cross-run artifact storage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory import JsonMemory, MemoryError, MEMORY_SCHEMA_VERSION
from .planner import order_tasks
from .policy import PolicyError, validate_task

CHECKPOINT_SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"
MANIFEST_FILENAME = "manifest.json"
TASK_STATES = frozenset({"pending", "running", "succeeded", "failed", "blocked"})


class CheckpointError(ValueError):
    """Raised when a checkpoint is corrupt, inconsistent, or unsupported."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _task_fingerprint(task: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(task)).hexdigest()


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_checkpoint_state(state: dict[str, Any]) -> None:
    """Validate task-level invariants, dependencies, and queue idempotency keys."""
    if not isinstance(state, dict) or state.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise CheckpointError("Unsupported queue memory schema")
    records = state.get("tasks")
    if not isinstance(records, dict):
        raise CheckpointError("Checkpoint tasks must be a JSON object")
    if not isinstance(state.get("incidents"), list) or not isinstance(state.get("solutions"), list):
        raise CheckpointError("Checkpoint incidents and solutions must be JSON arrays")

    payloads: list[dict[str, Any]] = []
    for task_id, record in records.items():
        if not isinstance(task_id, str) or not task_id or not isinstance(record, dict):
            raise CheckpointError("Checkpoint has an invalid task record key")
        payload = record.get("payload")
        try:
            validate_task(payload)
        except (PolicyError, TypeError) as exc:
            raise CheckpointError(f"Invalid task payload for {task_id}: {exc}") from exc
        if payload.get("id") != task_id:
            raise CheckpointError(f"Task payload ID mismatch for {task_id}")
        if record.get("status") not in TASK_STATES:
            raise CheckpointError(f"Invalid task status for {task_id}")
        attempts = record.get("attempts")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            raise CheckpointError(f"Invalid attempt counter for {task_id}")
        if attempts > payload.get("max_attempts", 3):
            raise CheckpointError(f"Attempt counter exceeds task policy for {task_id}")
        if record.get("idempotency_key") != _task_fingerprint(payload):
            raise CheckpointError(f"Idempotency key mismatch for {task_id}")
        # Confirm results can round-trip through JSON before checkpointing.
        try:
            json.dumps(record, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise CheckpointError(f"Task record for {task_id} is not JSON serializable") from exc
        payloads.append(payload)

    try:
        order_tasks(payloads)
    except PolicyError as exc:
        raise CheckpointError(f"Checkpoint dependency graph is invalid: {exc}") from exc


def export_checkpoint(memory_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Write a validated state file and SHA-256 manifest for upload as one artifact."""
    memory = JsonMemory(memory_path)
    try:
        state = memory.load()
    except MemoryError as exc:
        raise CheckpointError(str(exc)) from exc
    validate_checkpoint_state(state)

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    digest = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "state_schema_version": MEMORY_SCHEMA_VERSION,
        "filename": STATE_FILENAME,
        "sha256": digest,
        "task_count": len(state["tasks"]),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    # Manifest is installed last: consumers reject a partial or mismatched pair.
    _write_atomic(output / STATE_FILENAME, state_text)
    _write_atomic(
        output / MANIFEST_FILENAME,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def restore_checkpoint(checkpoint_dir: str | Path, memory_path: str | Path) -> dict[str, Any]:
    """Verify artifact metadata and task invariants before atomically replacing memory."""
    source = Path(checkpoint_dir).resolve()
    manifest_path = source / MANIFEST_FILENAME
    state_path = source / STATE_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state_text = state_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"Cannot read complete checkpoint: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError("Unsupported checkpoint manifest")
    if manifest.get("state_schema_version") != MEMORY_SCHEMA_VERSION or manifest.get("filename") != STATE_FILENAME:
        raise CheckpointError("Checkpoint manifest schema or filename is invalid")
    expected_digest = manifest.get("sha256")
    actual_digest = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise CheckpointError("Checkpoint SHA-256 value is malformed")
    if actual_digest != expected_digest:
        raise CheckpointError("Checkpoint integrity check failed: SHA-256 mismatch")

    try:
        state = json.loads(state_text)
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"Checkpoint state is invalid JSON: {exc}") from exc
    validate_checkpoint_state(state)
    if manifest.get("task_count") != len(state["tasks"]):
        raise CheckpointError("Checkpoint manifest task count does not match the state")

    try:
        JsonMemory(memory_path).save(state)
    except MemoryError as exc:
        raise CheckpointError(f"Cannot restore checkpoint memory: {exc}") from exc
    return {
        "restored": True,
        "task_count": len(state["tasks"]),
        "sha256": actual_digest,
        "memory_path": str(Path(memory_path)),
    }
