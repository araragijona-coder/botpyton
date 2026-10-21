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
    """A small durable queue; state transitions are serialized across processes."""

    def __init__(self, memory: JsonMemory):
        self.memory = memory

    def _load(self) -> dict[str, Any]:
        state = self.memory.load()
        self._validate_persisted_state(state)
        return state

    @staticmethod
    def _validate_persisted_state(state: dict[str, Any]) -> None:
        """Reject malformed durable queue records before operations index into them."""
        records = state.get("tasks")
        if not isinstance(records, dict):
            raise QueueError("Persisted task queue has an invalid tasks mapping")

        required_record_fields = {
            "payload", "idempotency_key", "status", "attempts", "created_at",
            "updated_at", "started_at", "finished_at", "last_error", "result",
        }
        payloads: list[dict[str, Any]] = []
        for task_id, record in records.items():
            if not isinstance(task_id, str) or not task_id.strip():
                raise QueueError("Persisted task queue contains an invalid task ID")
            if not isinstance(record, dict):
                raise QueueError(f"Persisted task {task_id!r} is not an object")
            missing = required_record_fields - record.keys()
            if missing:
                raise QueueError(
                    f"Persisted task {task_id!r} is missing fields: {', '.join(sorted(missing))}"
                )

            payload = record["payload"]
            if not isinstance(payload, dict):
                raise QueueError(f"Persisted task {task_id!r} has an invalid payload object")
            try:
                validate_task(payload)
                expected_fingerprint = _fingerprint(payload)
            except (PolicyError, TypeError, ValueError, RecursionError) as exc:
                raise QueueError(
                    f"Persisted task {task_id!r} has an invalid payload: {exc}"
                ) from exc
            if payload["id"] != task_id:
                raise QueueError(f"Persisted task key does not match payload ID for {task_id!r}")
            if record["idempotency_key"] != expected_fingerprint:
                raise QueueError(f"Persisted task {task_id!r} has an invalid idempotency fingerprint")

            status = record["status"]
            if not isinstance(status, str) or status not in TASK_STATES:
                raise QueueError(f"Persisted task {task_id!r} has an unknown status")
            attempts = record["attempts"]
            maximum = payload.get("max_attempts", 3)
            if (
                isinstance(attempts, bool) or not isinstance(attempts, int)
                or attempts < 0 or attempts > maximum
            ):
                raise QueueError(f"Persisted task {task_id!r} has an invalid attempt count")
            if not isinstance(record["created_at"], str) or not isinstance(record["updated_at"], str):
                raise QueueError(f"Persisted task {task_id!r} has invalid timestamps")
            for field in ("started_at", "finished_at", "last_error"):
                value = record[field]
                if value is not None and not isinstance(value, str):
                    raise QueueError(f"Persisted task {task_id!r} has invalid {field} metadata")
            payloads.append(payload)

        try:
            order_tasks(payloads)
        except (TypeError, ValueError, RecursionError) as exc:
            # In particular, do not let a missing dependency escape later as KeyError.
            raise QueueError(f"Persisted task dependency graph is invalid: {exc}") from exc

    def _save(self, state: dict[str, Any]) -> None:
        self.memory.save(state)

    def enqueue_many(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert tasks once; the entire read/validate/write cycle is exclusive."""
        with self.memory.transaction():
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

    def claim_next(
        self, *, allowed_task_ids: set[str] | frozenset[str] | None = None
    ) -> dict[str, Any] | None:
        """Claim one ready task, optionally restricting claims to an explicit task-ID set.

        The scope applies only to claiming. Dependency readiness is always checked
        against the full persisted queue, and unrelated pending tasks are not claimed.
        """
        if allowed_task_ids is not None and (
            not isinstance(allowed_task_ids, (set, frozenset))
            or any(not isinstance(task_id, str) or not task_id for task_id in allowed_task_ids)
        ):
            raise QueueError("allowed_task_ids must be a set of non-empty task ID strings")
        with self.memory.transaction():
            state = self._load()
            records = state["tasks"]
            while True:
                ready = []
                for task_id, record in records.items():
                    if allowed_task_ids is not None and task_id not in allowed_task_ids:
                        continue
                    if record["status"] != "pending":
                        continue
                    dependencies = record["payload"].get("depends_on", [])
                    if all(records[dep]["status"] == "succeeded" for dep in dependencies):
                        ready.append(task_id)
                if not ready:
                    self._refresh_blocked(state, task_ids=allowed_task_ids)
                    self._save(state)
                    return None

                # Fail any claimable-but-exhausted records in one batch. This avoids
                # recursively calling claim_next() for hand-edited/recovered states.
                exhausted = [
                    task_id for task_id in ready
                    if records[task_id]["attempts"] >= records[task_id]["payload"].get("max_attempts", 3)
                ]
                if exhausted:
                    for task_id in exhausted:
                        record = records[task_id]
                        record["status"] = "failed"
                        record["last_error"] = "retry_limit_reached"
                        record["updated_at"] = _now()
                        record["finished_at"] = _now()
                    self._refresh_blocked(state, task_ids=allowed_task_ids)
                    continue

                task_id = sorted(ready)[0]
                record = records[task_id]
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
        with self.memory.transaction():
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
        with self.memory.transaction():
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
        """Recover 'running' records in one exclusive transition."""
        with self.memory.transaction():
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

    def _refresh_blocked(
        self,
        state: dict[str, Any],
        *,
        task_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        records = state["tasks"]
        for task_id, record in records.items():
            if task_ids is not None and task_id not in task_ids:
                continue
            if record["status"] in {"pending", "blocked"}:
                deps = record["payload"].get("depends_on", [])
                failed = any(records[dep]["status"] in {"failed", "blocked"} for dep in deps)
                waiting = any(records[dep]["status"] != "succeeded" for dep in deps)
                record["status"] = "blocked" if failed else ("pending" if not waiting else "blocked")
                record["updated_at"] = _now()
