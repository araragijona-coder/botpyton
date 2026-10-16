"""Verification helpers; commands are fixed by the caller, never task text."""
from __future__ import annotations
import ast
from pathlib import Path

class VerificationError(ValueError):
    pass

def verify_python_files(root: str | Path) -> list[str]:
    """Parse tracked-style .py files under root without executing them."""
    base = Path(root).resolve()
    errors: list[str] = []
    for path in sorted(base.rglob("*.py")):
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            path.resolve().relative_to(base)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            errors.append(f"{path.relative_to(base)}: {exc}")
    return errors
