"""Small, schema-checked JSON persistence using atomic replacement."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MEMORY_SCHEMA_VERSION = 1

class MemoryError(ValueError):
    """Raised for invalid or unsupported persisted memory."""

class JsonMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": MEMORY_SCHEMA_VERSION, "tasks": {}, "incidents": [], "solutions": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryError(f"Cannot read memory file: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError("Memory schema is invalid or unsupported")
        for key, expected in (("tasks", dict), ("incidents", list), ("solutions", list)):
            if not isinstance(data.get(key), expected):
                raise MemoryError(f"Memory field {key!r} has the wrong type")
        return data

    def save(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError("Refusing to save unsupported memory schema")
        for key, expected in (("tasks", dict), ("incidents", list), ("solutions", list)):
            if not isinstance(data.get(key), expected):
                raise MemoryError(f"Memory field {key!r} has the wrong type")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
