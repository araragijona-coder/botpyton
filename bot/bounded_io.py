"""Small bounded-read helpers for local JSON and diagnostic inputs."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

DEFAULT_JSON_MAX_DEPTH = 64


class BoundedReadError(ValueError):
    """Raised when a local input is too large or changes during a bounded read."""


def _signature(info: os.stat_result) -> tuple[int, int, int]:
    # Compare stable file identity and size only. Windows stat/fstat timestamp
    # representations can differ despite referring to the same unchanged file.
    return (info.st_dev, info.st_ino, info.st_size)


def read_bounded_bytes(path: str | Path, *, limit_bytes: int, label: str = "Input file") -> bytes:
    """Read at most limit_bytes + 1 bytes, even if a file grows after stat()."""
    target = Path(path)
    if not isinstance(limit_bytes, int) or isinstance(limit_bytes, bool) or limit_bytes < 0:
        raise ValueError("limit_bytes must be a non-negative integer")
    try:
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            raise BoundedReadError(f"{label} must be a regular file")
        if before.st_size < 0 or before.st_size > limit_bytes:
            raise BoundedReadError(f"{label} exceeds {limit_bytes} bytes")
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
                raise BoundedReadError(f"{label} changed while it was being opened")
            raw = stream.read(limit_bytes + 1)
            after_open = os.fstat(stream.fileno())
        if len(raw) > limit_bytes or after_open.st_size > limit_bytes:
            raise BoundedReadError(f"{label} exceeds {limit_bytes} bytes")
        after_path = target.stat()
        if _signature(after_open) != _signature(opened) or _signature(after_path) != _signature(after_open):
            raise BoundedReadError(f"{label} changed during bounded reading")
        return raw
    except BoundedReadError:
        raise
    except OSError as exc:
        raise BoundedReadError(f"Cannot read {label}: {exc}") from exc


def enforce_json_depth(value: Any, *, max_depth: int = DEFAULT_JSON_MAX_DEPTH, label: str = "JSON input") -> None:
    """Reject deeply nested decoded JSON without recursive traversal or a large work stack."""
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    stack: list[tuple[Any, int]] = [(iter((value,)), 0)]
    while stack:
        children, parent_depth = stack[-1]
        try:
            current = next(children)
        except StopIteration:
            stack.pop()
            continue
        depth = parent_depth + 1
        if depth > max_depth:
            raise BoundedReadError(f"{label} exceeds the JSON nesting depth limit ({max_depth})")
        if isinstance(current, dict):
            stack.append((iter(current.values()), depth))
        elif isinstance(current, list):
            stack.append((iter(current), depth))
