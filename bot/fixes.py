"""Exact-pattern source fixes with backups and mandatory parser verification."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .policy import PROTECTED_PATH_PREFIXES

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}
_PROTECTED_EXACT = {".cerebro/config.json"}
_PROTECTED_EXTRA = (".cerebro/memory/", ".cerebro/reports/", ".cerebro/backups/")


def _iter_files(root: Path, suffix: str):
    base = root.resolve()
    for directory, names, filenames in os.walk(base, topdown=True, followlinks=False):
        names[:] = sorted(name for name in names if name not in _SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix.lower() != suffix:
                continue
            try:
                relative = path.resolve().relative_to(base).as_posix()
            except ValueError:
                continue
            yield path, relative


def _is_protected(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    if normalized in _PROTECTED_EXACT:
        return True
    prefixes = tuple(PROTECTED_PATH_PREFIXES) + (".github/",) + _PROTECTED_EXTRA
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _atomic_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup_original(root: Path, path: Path, relative: str, original: str) -> str:
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
    backup_dir = root / ".cerebro" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{digest}.{stamp}.bak"
    # Exclusive create prevents silent overwriting of another backup.
    with backup.open("x", encoding="utf-8", newline="") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    return backup.relative_to(root).as_posix()


def _no_fix(reason: str, *, rule: str | None = None) -> dict[str, Any]:
    return {
        "applied": False,
        "rule": rule,
        "reason": reason,
        "automatic_source_edit_applied": False,
    }


def _apply_verified_candidate(root: Path, path: Path, relative: str, original: str,
                              candidate: str, validator) -> dict[str, Any]:
    if _is_protected(relative):
        return _no_fix(f"Protected path cannot be changed automatically: {relative}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return _no_fix("Target path escapes the repository root")
    if path.is_symlink() or not path.is_file():
        return _no_fix("Target is not a regular repository file")
    if candidate == original:
        return _no_fix("Candidate does not change the source")
    try:
        validator(candidate)
    except (SyntaxError, json.JSONDecodeError, ValueError) as exc:
        return _no_fix(f"Candidate failed verification: {exc}")
    backup_path = _backup_original(root, path, relative, original)
    _atomic_write(path, candidate)
    return {
        "applied": True,
        "rule": "verified_known_pattern",
        "reason": "The exact repair pattern matched and the candidate passed its parser.",
        "path": relative,
        "backup_path": backup_path,
        "automatic_source_edit_applied": True,
        "verification": "candidate parses successfully",
    }


def _validate_python(source: str) -> None:
    ast.parse(source)


# Deliberately narrow: one-line compound-statement headers without comments.
# Multiline statements, decorators, strings/comments containing shell-like data,
# and all other syntax errors are out of scope.
_PY_HEADER = re.compile(
    r"^[ \t]*(?:"
    r"(?:async[ \t]+)?def[ \t]+[A-Za-z_]\w*[ \t]*\([^\r\n#]*\)"
    r"|(?:if|elif|for|while)[ \t]+[^\r\n#]+"
    r"|class[ \t]+[A-Za-z_]\w*(?:[ \t]*\([^\r\n#]*\))?"
    r")[ \t]*$"
)


def _candidate_python_colon(source: str) -> str | None:
    try:
        ast.parse(source)
        return None
    except SyntaxError as exc:
        if exc.msg != "expected ':'" or not exc.lineno:
            return None
        lines = source.splitlines(keepends=True)
        index = exc.lineno - 1
        if index < 0 or index >= len(lines):
            return None
        line = lines[index]
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if body.rstrip().endswith(":") or not _PY_HEADER.fullmatch(body):
            return None
        updated = body.rstrip() + ":" + ending
        candidate_lines = list(lines)
        candidate_lines[index] = updated
        candidate = "".join(candidate_lines)
        try:
            ast.parse(candidate)
        except SyntaxError:
            return None
        return candidate


def _fix_python_missing_colon(root: Path, log_text: str) -> dict[str, Any]:
    if "SyntaxError" not in log_text:
        return _no_fix("Failure log does not contain the SyntaxError signature")
    failures: list[tuple[Path, str, str, SyntaxError]] = []
    for path, relative in _iter_files(root, ".py"):
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source)
        except SyntaxError as exc:
            failures.append((path, relative, source, exc))
        except (OSError, UnicodeError):
            continue
    if len(failures) != 1:
        return _no_fix(f"Expected exactly one invalid Python file; found {len(failures)}")
    path, relative, source, exc = failures[0]
    if exc.msg != "expected ':'":
        return _no_fix(f"Syntax error is outside the exact missing-colon rule: {exc.msg}")
    candidate = _candidate_python_colon(source)
    if candidate is None:
        return _no_fix("No single-line compound header matched the safe missing-colon rule")
    return _apply_verified_candidate(root, path, relative, source, candidate, _validate_python)


def _fix_json_trailing_comma(root: Path, log_text: str) -> dict[str, Any]:
    if "JSONDecodeError" not in log_text:
        return _no_fix("Failure log does not contain the JSONDecodeError signature")
    invalid: list[tuple[Path, str, str, json.JSONDecodeError]] = []
    for path, relative in _iter_files(root, ".json"):
        try:
            source = path.read_text(encoding="utf-8")
            json.loads(source)
        except json.JSONDecodeError as exc:
            invalid.append((path, relative, source, exc))
        except (OSError, UnicodeError):
            continue
    if len(invalid) != 1:
        return _no_fix(f"Expected exactly one invalid JSON file; found {len(invalid)}")
    path, relative, source, exc = invalid[0]
    pos = exc.pos
    if pos >= len(source) or source[pos] not in "}]":
        return _no_fix("JSON parser position is not a closing object/array delimiter")
    comma = pos - 1
    while comma >= 0 and source[comma] in " \t\r\n":
        comma -= 1
    if comma < 0 or source[comma] != ",":
        return _no_fix("No trailing comma is immediately before the parser's closing delimiter")
    candidate = source[:comma] + source[comma + 1:]
    return _apply_verified_candidate(
        root, path, relative, source, candidate, lambda text: json.loads(text)
    )


def apply_known_fix(log_text: str, root: str | Path) -> dict[str, Any]:
    """Apply only an exact, verified and backed-up repair rule; never guess."""
    base = Path(root).resolve()
    if not base.is_dir():
        return _no_fix("Repository root is not a directory")
    # Ambiguous logs must not select the order-dependent first rule.
    if "SyntaxError" in log_text and "JSONDecodeError" in log_text:
        return _no_fix("Failure log contains multiple known signatures; manual diagnosis required")
    if "JSONDecodeError" in log_text:
        result = _fix_json_trailing_comma(base, log_text)
        if result["applied"]:
            result["rule"] = "json.remove_single_trailing_comma"
        return result
    if "SyntaxError" in log_text:
        result = _fix_python_missing_colon(base, log_text)
        if result["applied"]:
            result["rule"] = "python.add_missing_compound_header_colon"
        return result
    return _no_fix("No automatic source repair exists for this signature")
