"""Bounded syntax verification helpers; no project code is executed."""
from __future__ import annotations

import ast
import os
from pathlib import Path

MAX_PYTHON_FILES = 5000
MAX_PYTHON_FILE_BYTES = 2_000_000
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules", "dist",
    "build", "target", "vendor", ".next", ".nuxt", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache",
}


class VerificationError(ValueError):
    pass


def verify_python_files(
    root: str | Path,
    *,
    max_files: int = MAX_PYTHON_FILES,
    max_file_bytes: int = MAX_PYTHON_FILE_BYTES,
) -> list[str]:
    """Parse bounded Python source files without importing or executing them."""
    base = Path(root).resolve()
    if not base.is_dir():
        raise VerificationError(f"Python verification root is not a directory: {base}")
    if max_files < 1 or max_file_bytes < 1:
        raise VerificationError("Verification limits must be positive")
    errors: list[str] = []
    seen = 0
    truncated = False
    for directory, names, filenames in os.walk(base, topdown=True, followlinks=False):
        current = Path(directory)
        names[:] = sorted(
            name for name in names
            if name not in SKIP_DIRS and not (current / name).is_symlink()
        )
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = current / name
            if path.is_symlink():
                continue
            seen += 1
            if seen > max_files:
                truncated = True
                break
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(base).as_posix()
                size = resolved.stat().st_size
                if size > max_file_bytes:
                    errors.append(f"{relative}: skipped; source exceeds {max_file_bytes} bytes")
                    continue
                source = resolved.read_text(encoding="utf-8")
                if len(source.encode("utf-8")) > max_file_bytes:
                    errors.append(f"{relative}: skipped; decoded source exceeds {max_file_bytes} bytes")
                    continue
                ast.parse(source, filename=relative)
            except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
                try:
                    relative = path.relative_to(base).as_posix()
                except ValueError:
                    relative = path.name
                errors.append(f"{relative}: {type(exc).__name__}: {exc}")
        if truncated:
            break
    if truncated:
        errors.append(f"syntax verification incomplete: file limit {max_files} reached")
    return errors
