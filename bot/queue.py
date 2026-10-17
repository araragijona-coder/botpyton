"""Persistent JSON task queue with explicit states and safe resume semantics."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .memory import JsonMemory
from .planner import order_tasks
from .policy import PolicyError, validate_task

TASK_STATES = frozenset({"pending", "running", "succeeded", "failed", "blocked"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fingerprint(task: dict[str, Any]) -> str:
    canonical = json.dumps(task, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class QueueError(ValueError):
    """Raised when a queue operation would make state ambiguous or unsafe."""


class TaskQueue:
    """A small durable queue; each mutation is atomically saved as JSON."""

    def __init__(self, memory: JsonMemory):
        self.memory = memory

    def _load(self) -> dict[str, Any]:
        return self.memory.load()

    def _save(self, state: dict[str, Any]) -> None:
        self.memory.save(state)

    def enqueue_many(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert tasks once. Repeating identical IDs/payloads is a no-op."""
        if not isinstance(tasks, list) or not tasks:
            raise QueueError("enqueue_many requires a non-empty task list")
        state = self._load()
        existing = state["tasks"]
        combined: dict[str, dict[str, Any]] = {}
        for task_id, record in existing.items():
            combined[task_id] = record["payload"]
        for task in tasks:
            validate_task(task)
            task_id = task["id"]
            if task_id in combined and _fingerprint(combined[task_id]) != _fingerprint(task):
                raise QueueError(f"Idempotency conflict for task ID: {task_id}")
            combined[task_id] = task
        # Validate all dependencies and cycles before changing persisted state.
        order_tasks(list(combined.values()))
        inserted: list[dict[str, Any]] = []
        for task in tasks:
            task_id = task["id"]
            fingerprint = _fingerprint(task)
            if task_id in existing:
                if existing[task_id]["idempotency_key"] != fingerprint:
                    raise QueueError(f"Idempotency conflict for task ID: {task_id}")
                inserted.append(existing[task_id])
                continue
            record = {
                "payload": task,
                "idempotency_key": fingerprint,
                "status": "pending",
                "attempts": 0,
                "created_at": _now(),
                "updated_at": _now(),
                "started_at": None,
                "finished_at": None,
                "last_error": None,
                "result": None,
            }
            existing[task_id] = record
            inserted.append(record)
        self._save(state)
        return inserted

    def list_tasks(self) -> list[dict[str, Any]]:
        state = self._load()
        return [
            {"id": task_id, **record}
            for task_id, record in sorted(state["tasks"].items())
        ]

    def get(self, task_id: str) -> dict[str, Any]:
        record = self._load()["tasks"].get(task_id)
        if record is None:
            raise QueueError(f"Unknown task ID: {task_id}")
        return {"id": task_id, **record}

    def claim_next(self) -> dict[str, Any] | None:
        """Claim the first dependency-ready task; claim is persisted before return."""
        state = self._load()
        records = state["tasks"]
        ready = []
        for task_id, record in records.items():
            if record["status"] != "pending":
                continue
            dependencies = record["payload"].get("depends_on", [])
            if all(records[dep]["status"] == "succeeded" for dep in dependencies):
                ready.append(task_id)
        if not ready:
            self._refresh_blocked(state)
            self._save(state)
            return None
        task_id = sorted(ready)[0]
        record = records[task_id]
        if record["attempts"] >= record["payload"].get("max_attempts", 3):
            record["status"] = "failed"
            record["last_error"] = "retry_limit_reached"
            record["updated_at"] = _now()
            self._save(state)
            return self.claim_next()
        record["status"] = "running"
        record["attempts"] += 1
        record["started_at"] = _now()
        record["finished_at"] = None
        record["updated_at"] = _now()
        self._save(state)
        return {"id": task_id, **record}

    def complete(self, task_id: str, result: Any = None) -> dict[str, Any]:
        return self._transition(task_id, "succeeded", result=result)

    def fail(self, task_id: str, error: str, *, retryable: bool = False) -> dict[str, Any]:
        if not isinstance(error, str) or not error.strip():
            raise QueueError("Failure error must be a non-empty string")
        state = self._load()
        record = state["tasks"].get(task_id)
        if record is None:
            raise QueueError(f"Unknown task ID: {task_id}")
        if record["status"] != "running":
            raise QueueError(f"Cannot fail task {task_id} from state {record['status']}")
        if retryable and record["attempts"] < record["payload"].get("max_attempts", 3):
            status = "pending"
        else:
            status = "failed"
        record.update(status=status, last_error=error, updated_at=_now(),
                       finished_at=None if status == "pending" else _now())
        self._refresh_blocked(state)
        self._save(state)
        return {"id": task_id, **record}

    def _transition(self, task_id: str, status: str, *, result: Any = None) -> dict[str, Any]:
        state = self._load()
        record = state["tasks"].get(task_id)
        if record is None:
            raise QueueError(f"Unknown task ID: {task_id}")
        if record["status"] == status:
            # Completion retries are idempotent only for the same serialized result.
            if record.get("result") != result:
                raise QueueError(f"Idempotency conflict completing task: {task_id}")
            return {"id": task_id, **record}
        if record["status"] != "running":
            raise QueueError(f"Cannot transition task {task_id} from {record['status']} to {status}")
        record.update(status=status, result=result, last_error=None,
                      updated_at=_now(), finished_at=_now())
        self._refresh_blocked(state)
        self._save(state)
        return {"id": task_id, **record}

    def resume_interrupted(self) -> list[str]:
        """Recover 'running' records after a process/runner interruption."""
        state = self._load()
        resumed = []
        for task_id, record in state["tasks"].items():
            if record["status"] == "running":
                if record["attempts"] >= record["payload"].get("max_attempts", 3):
                    record["status"] = "failed"
                    record["last_error"] = "interrupted_retry_limit_reached"
                    record["finished_at"] = _now()
                else:
                    record["status"] = "pending"
                    record["last_error"] = "interrupted_previous_run"
                    resumed.append(task_id)
                record["updated_at"] = _now()
        self._refresh_blocked(state)
        self._save(state)
        return sorted(resumed)

    def _refresh_blocked(self, state: dict[str, Any]) -> None:
        records = state["tasks"]
        for record in records.values():
            if record["status"] in {"pending", "blocked"}:
                deps = record["payload"].get("depends_on", [])
                failed = any(records[dep]["status"] in {"failed", "blocked"} for dep in deps)
                waiting = any(records[dep]["status"] != "succeeded" for dep in deps)
                record["status"] = "blocked" if failed else ("pending" if not waiting else "blocked")
                record["updated_at"] = _now()
