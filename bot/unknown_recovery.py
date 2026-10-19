"""Bounded, deterministic investigation plans for failures with no known rule.

The investigator performs static checks only. It never executes project code,
modifies project files, installs dependencies, or retries a failing command.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

from .bounded_io import BoundedReadError, read_bounded_bytes
from .recovery import classify_failure

MAX_LOG_BYTES = 64 * 1024
MAX_SCAN_ENTRIES = 5_000
MAX_SCAN_FILES = 500
MAX_FILE_BYTES = 200_000
MAX_TOTAL_READ_BYTES = 2 * 1024 * 1024
MAX_FINDINGS = 20
SKIP_DIRS = frozenset({
    ".git", ".cerebro", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".idea",
    "coverage", "vendor", "target", "bin", "obj",
})
SOURCE_SUFFIXES = frozenset({".py", ".pyi"})
JSON_SUFFIXES = frozenset({".json"})
_SIGNAL_FILES = (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py",
    "setup.cfg", "package.json", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "tsconfig.json", "go.mod", "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "global.json",
    "Directory.Build.props", "Makefile", "Justfile",
)


class UnknownRecoveryError(ValueError):
    """Raised when a failure-investigation request is invalid or exceeds bounds."""


def _redact(text: str, limit: int = 1200) -> str:
    value = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~-]+",
        r"\1[REDACTED]",
        text,
    )
    value = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b",
        "[REDACTED_GITHUB_TOKEN]",
        value,
    )
    value = re.sub(
        r"(?i)(\b(?:token|secret|password|api[_-]?key|private[_-]?key)\b\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        value,
    )
    return value[:limit]


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _validate_root(root: str | Path) -> Path:
    lexical = Path(root).expanduser()
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    current = Path(os.path.abspath(lexical))
    chain = []
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for part in reversed(chain):
        try:
            part.lstat()
        except OSError as exc:
            raise UnknownRecoveryError("Project root path cannot be inspected safely") from exc
        if _is_link_or_reparse(part):
            raise UnknownRecoveryError("Project root cannot contain a symlink or reparse-point component")
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnknownRecoveryError("Project root does not resolve to an existing directory") from exc
    if not resolved.is_dir():
        raise UnknownRecoveryError("Project root must be an existing directory")
    return resolved


def _project_files(root: Path) -> tuple[list[Path], dict[str, Any]]:
    """Find a deterministic, bounded file set without following directory links."""
    selected: list[Path] = []
    entries_seen = 0
    skipped_links = 0
    truncated = False
    base = root.resolve()
    for directory, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        current = Path(directory)
        kept = []
        for name in sorted(dirnames):
            child = current / name
            if name in SKIP_DIRS:
                continue
            try:
                if _is_link_or_reparse(child):
                    skipped_links += 1
                    continue
            except OSError:
                skipped_links += 1
                continue
            kept.append(name)
        dirnames[:] = kept
        filenames.sort()
        for name in filenames:
            entries_seen += 1
            if entries_seen > MAX_SCAN_ENTRIES or len(selected) >= MAX_SCAN_FILES:
                truncated = True
                break
            path = current / name
            try:
                info = path.lstat()
                if _is_link_or_reparse(path) or not path.is_file():
                    skipped_links += 1
                    continue
                relative = path.relative_to(base).as_posix()
            except (OSError, ValueError):
                skipped_links += 1
                continue
            suffix = path.suffix.lower()
            if suffix in SOURCE_SUFFIXES or suffix in JSON_SUFFIXES or path.name == "pyproject.toml" or relative in {
                "package.json", "tsconfig.json",
            }:
                selected.append(path)
        if truncated:
            break
    selected.sort(key=lambda path: path.relative_to(base).as_posix())
    return selected, {
        "entries_examined": min(entries_seen, MAX_SCAN_ENTRIES),
        "candidate_files": len(selected),
        "scan_truncated": truncated,
        "link_or_special_entries_skipped": skipped_links,
        "file_limit": MAX_SCAN_FILES,
        "entry_limit": MAX_SCAN_ENTRIES,
        "per_file_bytes_limit": MAX_FILE_BYTES,
        "total_read_bytes_limit": MAX_TOTAL_READ_BYTES,
    }


def _static_probes(root: Path) -> dict[str, Any]:
    files, scan = _project_files(root)
    python_findings: list[dict[str, Any]] = []
    json_findings: list[dict[str, Any]] = []
    toml_findings: list[dict[str, Any]] = []
    python_checked = json_checked = toml_checked = 0
    total_read = 0
    files_skipped_for_budget = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        is_toml = path.name == "pyproject.toml"
        if (suffix not in SOURCE_SUFFIXES and suffix not in JSON_SUFFIXES and not is_toml):
            continue
        try:
            info = path.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                or not stat.S_ISREG(info.st_mode)
            ):
                files_skipped_for_budget += 1
                continue
            remaining_bytes = MAX_TOTAL_READ_BYTES - total_read
            if remaining_bytes <= 0:
                files_skipped_for_budget += 1
                continue
            read_limit = min(MAX_FILE_BYTES, remaining_bytes)
            if info.st_size > read_limit:
                files_skipped_for_budget += 1
                continue
            raw = read_bounded_bytes(path, limit_bytes=read_limit, label="Investigation file")
            # The entry may have changed between inventory and read. Discard bytes
            # if it became a symlink/reparse point or disappeared during the read.
            if _is_link_or_reparse(path):
                files_skipped_for_budget += 1
                continue
        except (OSError, BoundedReadError, ValueError):
            # A concurrently removed, replaced, or inaccessible candidate is not
            # reliable evidence; skip it and keep the partial-scan accounting.
            files_skipped_for_budget += 1
            continue
        total_read += len(raw)
        if suffix in SOURCE_SUFFIXES:
            python_checked += 1
            try:
                ast.parse(raw.decode("utf-8"), filename=relative)
            except SyntaxError as exc:
                if len(python_findings) < MAX_FINDINGS:
                    python_findings.append({
                        "path": relative,
                        "line": exc.lineno,
                        "column": exc.offset,
                        "error": _redact(exc.msg, 300),
                    })
            except (UnicodeError, ValueError, RecursionError) as exc:
                if len(python_findings) < MAX_FINDINGS:
                    python_findings.append({
                        "path": relative, "line": None, "column": None,
                        "error": f"static_parse_{type(exc).__name__}",
                    })
        elif suffix in JSON_SUFFIXES:
            json_checked += 1
            try:
                json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                if len(json_findings) < MAX_FINDINGS:
                    json_findings.append({
                        "path": relative,
                        "line": getattr(exc, "lineno", None),
                        "column": getattr(exc, "colno", None),
                        "error": _redact(str(exc), 300),
                    })
        elif is_toml:
            toml_checked += 1
            try:
                tomllib.loads(raw.decode("utf-8"))
            except (UnicodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
                if len(toml_findings) < MAX_FINDINGS:
                    toml_findings.append({
                        "path": relative,
                        "error": _redact(str(exc), 300),
                    })

    markers = []
    for relative in _SIGNAL_FILES:
        candidate = root / relative
        if candidate.is_file() and not _is_link_or_reparse(candidate):
            try:
                candidate.resolve(strict=True).relative_to(root)
                markers.append(relative)
            except (OSError, ValueError):
                continue
    return {
        "python": {
            "files_checked": python_checked,
            "syntax_errors": python_findings,
            "status": "issues_found" if python_findings else ("checked" if python_checked else "not_checked"),
        },
        "json": {
            "files_checked": json_checked,
            "invalid_files": json_findings,
            "status": "issues_found" if json_findings else ("checked" if json_checked else "not_checked"),
        },
        "toml": {
            "files_checked": toml_checked,
            "invalid_files": toml_findings,
            "status": "issues_found" if toml_findings else ("checked" if toml_checked else "not_checked"),
        },
        "project_markers": markers,
        "scan": {
            **scan,
            "bytes_read": total_read,
            "files_skipped_for_budget": files_skipped_for_budget,
        },
        "code_executed": False,
        "files_modified": False,
        "network_used": False,
    }


def _hypotheses(log_text: str, probes: dict[str, Any], expected: str | None, observed: str | None) -> list[dict[str, Any]]:
    """Return hypotheses only when supported by a testable signal."""
    lower = log_text.casefold()
    result: list[dict[str, Any]] = []

    def add(identifier: str, title: str, evidence: list[str], experiment: str, falsifies: str, priority: int) -> None:
        result.append({
            "id": identifier,
            "title": title,
            "priority": priority,
            "evidence": evidence,
            "experiment": experiment,
            "falsified_when": falsifies,
            "root_cause_confirmed": False,
            "source_changes_proposed": False,
        })

    py_errors = probes["python"]["syntax_errors"]
    if py_errors:
        first = py_errors[0]
        add(
            "HYP-PY-SYNTAX-01", "A Python source file may fail static parsing",
            [f"static.python.syntax_errors:{first['path']}:{first.get('line')}"],
            "Review the exact parser location, then rerun only the syntax checker; do not execute the file.",
            "The cited file parses successfully from the same revision and interpreter.",
            100,
        )
    json_errors = probes["json"]["invalid_files"]
    if json_errors:
        first = json_errors[0]
        add(
            "HYP-JSON-INPUT-01", "A JSON file may be malformed or encoded unexpectedly",
            [f"static.json.invalid_files:{first['path']}"],
            "Validate the cited file read-only and compare it with the producer's schema and a known-good fixture.",
            "The same bytes parse as JSON and satisfy the expected schema.",
            98,
        )
    toml_errors = probes["toml"]["invalid_files"]
    if toml_errors:
        first = toml_errors[0]
        add(
            "HYP-TOML-CONFIG-01", "A TOML project configuration may be invalid",
            [f"static.toml.invalid_files:{first['path']}"],
            "Run a TOML parse on the exact file and inspect the reported location before proposing any edit.",
            "The same file parses under the project's declared toolchain.",
            96,
        )
    if any(marker in lower for marker in ("assertionerror", "expected:", "expected ", "actual:", "got ")):
        if expected or observed:
            evidence = ["log.contains_test_contract_signal", "brief.expected_observed_values_present"]
        else:
            evidence = ["log.contains_test_contract_signal"]
        add(
            "HYP-TEST-CONTRACT-01", "The observed behavior may disagree with the test contract",
            evidence,
            "Capture exact expected and observed values, then reduce the failing case to one deterministic fixture.",
            "A minimal fixture does not reproduce the mismatch or the expected value is shown to be incorrect by the written contract.",
            65,
        )
    if any(term in lower for term in ("timed out", "timeout", "deadline exceeded", "killed by signal", "out of memory", "oom-kill")):
        add(
            "HYP-RESOURCE-BOUND-01", "The failure may be caused by a time or resource bound",
            ["log.contains_timeout_or_resource_signal"],
            "Repeat only the smallest permitted read-only/static step while recording elapsed time, exit code, and available memory; do not simply raise limits.",
            "The failure reproduces quickly with stable memory use and no timeout/resource signal.",
            62,
        )
    if any(term in lower for term in ("permission denied", "access is denied", "eacces", "eperm", "sharing violation")):
        add(
            "HYP-ACCESS-BOUNDARY-01", "The failing operation may cross an access or file-lock boundary",
            ["log.contains_permission_or_lock_signal"],
            "Inspect the exact path, current process identity, granted folder permission, and file-lock state without elevation.",
            "The same operation succeeds under the same identity and path without permission or lock errors.",
            62,
        )
    if any(term in lower for term in ("module not found", "no module named", "cannot find package", "package does not exist")):
        add(
            "HYP-TOOLCHAIN-01", "The active runtime or dependency contract may differ from the project's declaration",
            ["log.contains_missing_module_signal"],
            "Compare the active interpreter/toolchain with existing manifests and lockfiles; do not install, upgrade, or access the network.",
            "The intended runtime is confirmed and the declared dependency is already present in that exact environment.",
            60,
        )
    if any(term in lower for term in ("connection refused", "name resolution", "dns", "tls handshake", "network is unreachable")):
        add(
            "HYP-EXTERNAL-SERVICE-01", "The failure may depend on an external service or network path",
            ["log.contains_external_connection_signal"],
            "Check whether the task is intended to require a network, and inspect a sanitized status/error code; do not retry external writes automatically.",
            "The failure reproduces with the network-dependent step disabled or on a fully local fixture.",
            55,
        )

    result.sort(key=lambda item: (-item["priority"], item["id"]))
    return result[:8]


def investigate_failure(
    log_text: str,
    root: str | Path,
    *,
    command: str | None = None,
    exit_code: int | None = None,
    expected: str | None = None,
    observed: str | None = None,
    attempt: int = 1,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Build a deterministic failure hypothesis/experiment plan using static evidence only."""
    if not isinstance(log_text, str):
        raise UnknownRecoveryError("Failure log must be text")
    raw_log = log_text.encode("utf-8", errors="replace")
    if len(raw_log) > MAX_LOG_BYTES:
        raise UnknownRecoveryError(f"Failure log exceeds {MAX_LOG_BYTES} bytes")
    if not log_text.strip():
        raise UnknownRecoveryError("Failure log is empty")
    if exit_code is not None and (
        not isinstance(exit_code, int) or isinstance(exit_code, bool) or not -255 <= exit_code <= 255
    ):
        raise UnknownRecoveryError("exit_code must be an integer from -255 to 255")
    for label, value in (("command", command), ("expected", expected), ("observed", observed)):
        if value is not None and not isinstance(value, str):
            raise UnknownRecoveryError(f"{label} must be a string when supplied")
        if isinstance(value, str) and len(value) > 2000:
            raise UnknownRecoveryError(f"{label} exceeds 2000 characters")

    base = _validate_root(root)
    classification = classify_failure(log_text, attempt=attempt, max_attempts=max_attempts)
    probes = _static_probes(base)
    hypotheses = _hypotheses(log_text, probes, expected, observed)
    missing: list[str] = []
    if not command:
        missing.append("exact_command_and_arguments")
    if exit_code is None:
        missing.append("process_exit_code")
    if not expected:
        missing.append("expected_behavior_or_contract")
    if not observed:
        missing.append("observed_behavior_and_reproduction_steps")
    if probes["scan"]["scan_truncated"]:
        missing.append("remaining_static_scan_scope")
    if not hypotheses:
        missing.extend([
            "complete_unsanitized_log_kept_locally_for_manual_review",
            "minimal_reproduction_or_failing_test_id",
            "toolchain_versions_and_environment",
        ])

    if hypotheses:
        status = "investigation_complete"
        next_step = "Review the highest-priority hypothesis and perform only its single discriminating experiment. Re-run investigation after new evidence."
    else:
        status = "needs_more_evidence"
        next_step = "No defensible root-cause hypothesis emerged. Do not patch or retry; supply the missing reproduction evidence and investigate one variable at a time."
    if classification.category == "attempt_limit":
        status = "blocked_attempt_limit"
        next_step = "The configured attempt bound was exceeded. Stop this recovery cycle and request human review."
    if any(probes[key]["status"] == "issues_found" for key in ("python", "json", "toml")):
        recommended_resolution = "Review each exact static finding, create or update a focused regression, then use the existing isolated reviewed-patch workflow for any change. No automatic edit is justified by static evidence alone."
    elif hypotheses:
        recommended_resolution = "Run the listed discriminating experiment and update the evidence bundle. Apply a minimal reviewed change only after one cause is distinguished and a regression test captures it."
    else:
        recommended_resolution = "No change proposal: preserve source and input data, collect the missing evidence, and ask for human help if the error still cannot be reproduced deterministically."

    command_safe = _redact(command or "", 2000)
    return {
        "schema_version": 1,
        "status": status,
        "category": classification.category,
        "knowledge_rule_id": classification.rule_id,
        "classification_reason": classification.reason,
        "log_sha256": hashlib.sha256(raw_log).hexdigest(),
        "log_bytes": len(raw_log),
        "last_error_line": _redact(next((line.strip() for line in reversed(log_text.splitlines()) if line.strip()), "")),
        "command": command_safe or None,
        "exit_code": exit_code,
        "expected": _redact(expected or "", 1000) or None,
        "observed": _redact(observed or "", 1000) or None,
        "environment": {
            "os_name": os.name,
            "python_version": sys.version.split()[0],
        },
        "static_evidence": probes,
        "hypotheses": hypotheses,
        "missing_evidence": list(dict.fromkeys(missing)),
        "recommended_resolution": recommended_resolution,
        "next_step": next_step,
        "root_cause_confirmed": False,
        "automatic_retry_permitted": False,
        "automatic_source_edit_applied": False,
        "project_code_executed": False,
        "network_used": False,
        "ai_enabled": False,
    }
