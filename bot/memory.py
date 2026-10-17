"""Small, size-limited schema-checked JSON persistence using atomic replacement."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .bounded_io import BoundedReadError, enforce_json_depth, read_bounded_bytes
from .file_lock import exclusive_file_lock

MEMORY_SCHEMA_VERSION = 1
MAX_MEMORY_BYTES = 4 * 1024 * 1024
MAX_MEMORY_JSON_DEPTH = 64


class MemoryError(ValueError):
    """Raised for invalid or unsupported persisted memory."""


class _LimitedTextWriter:
    """Account UTF-8 bytes before forwarding each JSON encoder chunk."""

    def __init__(self, stream, limit_bytes: int):
        self.stream = stream
        self.limit_bytes = limit_bytes
        self.bytes_written = 0

    def write(self, text: str) -> int:
        size = len(text.encode("utf-8"))
        if self.bytes_written + size > self.limit_bytes:
            raise MemoryError(f"Memory state exceeds {self.limit_bytes} bytes")
        written = self.stream.write(text)
        self.bytes_written += size
        return written


def _enforce_memory_depth(value: Any) -> None:
    try:
        enforce_json_depth(value, max_depth=MAX_MEMORY_JSON_DEPTH, label="Memory state")
    except BoundedReadError as exc:
        raise MemoryError(str(exc)) from exc


class JsonMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold an exclusive thread/process lock across a read-modify-write cycle."""
        target = self.path.expanduser().resolve(strict=False)
        lock_path = target.with_name(f".{target.name}.botpyton.lock")
        with exclusive_file_lock(lock_path):
            yield

    def load(self) -> dict[str, Any]:
        # Reentrant locking lets TaskQueue hold the same transaction across its
        # entire read-modify-write operation while individual loads remain safe.
        with self.transaction():
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": MEMORY_SCHEMA_VERSION, "tasks": {}, "incidents": [], "solutions": []}
        try:
            raw = read_bounded_bytes(
                self.path, limit_bytes=MAX_MEMORY_BYTES, label="Memory file"
            )
            data = json.loads(raw.decode("utf-8"))
            _enforce_memory_depth(data)
        except (BoundedReadError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise MemoryError(f"Cannot read memory file safely: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError("Memory schema is invalid or unsupported")
        for key, expected in (("tasks", dict), ("incidents", list), ("solutions", list)):
            if not isinstance(data.get(key), expected):
                raise MemoryError(f"Memory field {key!r} has the wrong type")
        return data

    def save(self, data: dict[str, Any]) -> None:
        with self.transaction():
            self._save_unlocked(data)

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError("Refusing to save unsupported memory schema")
        for key, expected in (("tasks", dict), ("incidents", list), ("solutions", list)):
            if not isinstance(data.get(key), expected):
                raise MemoryError(f"Memory field {key!r} has the wrong type")
        _enforce_memory_depth(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                limited = _LimitedTextWriter(stream, MAX_MEMORY_BYTES)
                json.dump(data, limited, ensure_ascii=False, indent=2, sort_keys=True)
                limited.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except RecursionError as exc:
            raise MemoryError(f"Memory state is too deeply nested to serialize safely: {exc}") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
