"""Evidence-based, non-destructive diagnostics for known failure signatures."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .recovery import RecoveryDecision, classify_failure
from .verifier import verify_python_files

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}


def _files(root: Path, suffix: str | None = None):
    base = root.resolve()
    for directory, names, filenames in __import__("os").walk(base, followlinks=False):
        names[:] = sorted(name for name in names if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink() or (suffix and path.suffix.lower() != suffix):
                continue
            try:
                path.resolve().relative_to(base)
            except ValueError:
                continue
            yield path


def _dependency_evidence(root: Path, log_text: str) -> dict[str, Any]:
    match = re.search(r"No module named ['\"]([^'\"]+)", log_text)
    missing = match.group(1) if match else None
    declared: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            declared = list(project.get("dependencies", []))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            return {"missing_module": missing, "declared_dependencies": [], "pyproject_error": str(exc)}
    matches = [item for item in declared if missing and re.search(
        rf"(?<![A-Za-z0-9_-]){re.escape(missing.split('.')[0])}(?![A-Za-z0-9_-])",
        item, re.IGNORECASE)]
    return {
        "missing_module": missing,
        "declared_dependencies": declared,
        "matching_declared_dependencies": matches,
        "recommendation": (
            "Install the already-declared project dependencies in the test environment."
            if matches else
            "Check the import name and project metadata; do not install packages automatically."
        ),
    }


def _json_evidence(root: Path) -> dict[str, Any]:
    invalid = []
    checked = 0
    for path in _files(root, ".json"):
        checked += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            invalid.append({
                "path": path.relative_to(root.resolve()).as_posix(),
                "error": str(exc),
                "line": getattr(exc, "lineno", None),
                "column": getattr(exc, "colno", None),
            })
    return {"json_files_checked": checked, "invalid_json": invalid}


def _conflict_evidence(root: Path) -> dict[str, Any]:
    markers = []
    for path in _files(root):
        if path.suffix.lower() not in {".py", ".json", ".md", ".toml", ".yml", ".yaml", ".txt", ".ini", ".cfg"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        marker_lines = [
            number for number, line in enumerate(text.splitlines(), 1)
            if line.startswith(("<<<<<<< ", "=======", ">>>>>>> "))
        ]
        if marker_lines:
            markers.append({"path": path.relative_to(root.resolve()).as_posix(), "lines": marker_lines[:20]})
    return {"conflict_marker_files": markers}



def _redact_sensitive_text(value: str) -> str:
    """Avoid copying common credential values from logs or source snippets into reports."""
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~-]+",
        r"\1[REDACTED]",
        value,
    )
    text = re.sub(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b",
                  "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(
        r"(?i)(\b(?:token|secret|password|api[_-]?key|private[_-]?key)\b\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return text[:1000]


def _redact_source_line(value: str) -> str:
    if re.search(r"(?i)\b(?:token|secret|password|api[_-]?key|private[_-]?key)\b", value):
        return "[REDACTED: credential-like identifier in source line]"
    return _redact_sensitive_text(value)[:300]


def _traceback_evidence(log_text: str, root: Path) -> dict[str, Any]:
    """Extract bounded traceback context from files inside the project without executing them."""
    frame_pattern = re.compile(r"""File ["']([^"']+)["'], line (\d+)""")
    frames = []
    base = root.resolve()
    for match in frame_pattern.finditer(log_text):
        if len(frames) >= 10:
            break
        raw_path, raw_line = match.group(1), match.group(2)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(base).as_posix()
        except (OSError, ValueError):
            frames.append({"path": raw_path[:300], "line": int(raw_line), "source_read": False,
                           "reason": "frame path is outside project or unresolved"})
            continue
        line_number = int(raw_line)
        frame = {"path": relative, "line": line_number, "source_read": False}
        try:
            if not resolved.is_file() or resolved.is_symlink() or resolved.stat().st_size > 200_000:
                frame["reason"] = "source is missing, symbolic, or larger than 200 KB"
            else:
                lines = resolved.read_text(encoding="utf-8").splitlines()
                if 1 <= line_number <= len(lines):
                    start = max(1, line_number - 2)
                    end = min(len(lines), line_number + 2)
                    frame["context"] = [
                        {"line": number, "text": _redact_source_line(lines[number - 1])}
                        for number in range(start, end + 1)
                    ]
                    frame["source_read"] = True
                else:
                    frame["reason"] = "traceback line is outside current file length"
        except (OSError, UnicodeError) as exc:
            frame["reason"] = f"source read failed: {type(exc).__name__}"
        frames.append(frame)
    last_line = _redact_sensitive_text(
        next((line.strip() for line in reversed(log_text.splitlines()) if line.strip()), "")
    )
    error_match = re.search(
        r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failure|Interrupt))\b(?::|$)",
        last_line,
    )
    error_type = error_match.group(1) if error_match else None
    experiment_hints = {
        "NameError": "Check the exact identifier spelling, import, and lexical scope at the reported frame; search for its definition before editing.",
        "AttributeError": "Inspect the object's initialization and runtime type; compare the method/property with the installed library version.",
        "TypeError": "Compare the callable signature with the supplied argument count and types; create a minimal reproduction before changing code.",
        "KeyError": "Record the actual mapping keys and input shape at the failing frame; do not assume the data schema.",
        "IndexError": "Check sequence length and index calculation using the failing input; add a boundary test before changing the algorithm.",
        "FileNotFoundError": "Resolve the path against the process working directory and inspect whether the file is generated or expected to exist.",
        "PermissionError": "Check path, file ownership, permissions and file locks; do not elevate privileges automatically.",
        "ModuleNotFoundError": "Compare the import name with declared dependencies and the active interpreter; do not install packages automatically.",
        "AssertionError": "Capture expected and actual values and confirm whether the implementation or the test contract is wrong.",
        "TimeoutExpired": "Identify the slowest isolated step and reproduce with a bounded input; do not merely increase the timeout.",
        "SyntaxError": "Inspect the exact reported line and nearby delimiters/indentation; use the language parser to verify the minimal correction.",
    }
    return {
        "traceback_frames": frames,
        "last_error_line": last_line[:1000],
        "error_type_guess": error_type,
        "suggested_experiment": experiment_hints.get(
            error_type,
            "Form one hypothesis from the closest project traceback frame and test it with a minimal, reversible experiment.",
        ),
        "evidence_only": True,
        "source_modified": False,
        "code_executed": False,
    }

def diagnose_failure(
    log_text: str, root: str | Path, *, attempt: int = 1, max_attempts: int = 3
) -> dict[str, Any]:
    """Classify a failure and gather local evidence; never edits source files."""
    base = Path(root).resolve()
    decision: RecoveryDecision = classify_failure(
        log_text, attempt=attempt, max_attempts=max_attempts
    )
    details: dict[str, Any] = _traceback_evidence(log_text, base)
    if decision.strategy == "verify_declared_dependencies":
        details.update(_dependency_evidence(base, log_text))
    elif decision.strategy == "inspect_syntax_location":
        details.update({
            "python_syntax_errors": verify_python_files(base),
            "recommendation": "Inspect the exact file and line; no generic syntax rewrite is safe."
        })
    elif decision.strategy == "validate_json_schema":
        details.update({
            **_json_evidence(base),
            "recommendation": "Correct the reported JSON syntax error, then rerun validation."
        })
    elif decision.strategy == "rebuild_isolated_branch":
        details.update({
            **_conflict_evidence(base),
            "recommendation": "Resolve only identified conflict markers on an isolated branch; no merge was performed."
        })
    if decision.strategy == "inspect_import_contract":
        details.update({
            **_dependency_evidence(base, log_text),
            "recommendation": "Compare the imported symbol and package API/version; do not install or upgrade dependencies automatically.",
        })
    if decision.category == "unknown":
        details["recommendation"] = (
            "No exact known-error rule matched. Preserve the complete log, inspect the bounded "
            "traceback context, form one testable hypothesis, and run a small reversible experiment "
            "only when evidence can distinguish the cause; ask for missing evidence otherwise. "
            "No source edit or automatic retry was performed."
        )
    details.update({
        "knowledge_rule_id": decision.rule_id,
        "diagnostic_steps": list(decision.diagnostic_steps),
        "safe_experiment": decision.safe_experiment,
        "stop_condition": decision.stop_condition,
        "reference_url": decision.reference_url,
        "root_cause_confirmed": False,
        "automatic_retry_permitted": decision.retryable,
    })
    return {
        "category": decision.category,
        "strategy": decision.strategy,
        "retryable": decision.retryable,
        "reason": decision.reason,
        "automatic_retry_permitted": decision.retryable,
        "requires_new_evidence_before_retry": not decision.retryable,
        "automatic_source_edit_applied": False,
        "details": details,
    }
