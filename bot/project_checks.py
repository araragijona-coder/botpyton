"""Static discovery and explicitly authorized execution of common project checks."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable

from .process_runner import ProcessRunnerError, run_process

MAX_MANIFEST_BYTES = 200_000


class ProjectCheckError(ValueError):
    """Raised when a project check is unknown or not explicitly authorized."""


def _read_json_manifest(path: Path) -> dict | None:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def discover_project_checks(
    root: str | Path,
    *,
    which: Callable[[str], str | None] | None = None,
) -> dict:
    """Detect fixed check profiles without executing manifests, tests, builds or scripts."""
    find = which or shutil.which
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ProjectCheckError("Project root must be an existing directory")
    checks = []

    def add(check_id: str, ecosystem: str, description: str, executable: str | None,
            args: list[str], *, source: str, review: str = "Project code may execute; inspect it before approval.") -> None:
        if executable is None:
            return
        checks.append({
            "check_id": check_id,
            "ecosystem": ecosystem,
            "description": description,
            "source": source,
            "executable": executable,
            "argv": [executable, *args],
            "requires_project_code_execution_approval": True,
            "review_warning": review,
        })

    if (base / "tests").is_dir():
        add("python-unittest", "Python", "Python unittest discovery", sys.executable,
            ["-m", "unittest", "discover", "-s", "tests", "-v"], source="tests/ directory")

    package = _read_json_manifest(base / "package.json")
    scripts = package.get("scripts", {}) if package else {}
    if isinstance(scripts, dict):
        npm = find("npm")
        if isinstance(scripts.get("test"), str) and scripts["test"].strip():
            add("npm-test", "JavaScript/TypeScript", "Run package.json test script", npm,
                ["test"], source="package.json:scripts.test")
        if isinstance(scripts.get("build"), str) and scripts["build"].strip():
            add("npm-build", "JavaScript/TypeScript", "Run package.json build script", npm,
                ["run", "build"], source="package.json:scripts.build")

    if (base / "Cargo.toml").is_file():
        add("cargo-test", "Rust", "Compile and run Rust tests", find("cargo"),
            ["test"], source="Cargo.toml")
    if (base / "go.mod").is_file():
        add("go-test", "Go", "Compile and run Go tests", find("go"),
            ["test", "./..."], source="go.mod")
    if (base / "pom.xml").is_file():
        add("maven-test", "Java", "Run Maven test lifecycle", find("mvn"),
            ["test"], source="pom.xml")
    if any(base.glob("*.sln")) or any(base.glob("*.csproj")):
        add("dotnet-test", "C#/.NET", "Build solution and run .NET tests", find("dotnet"),
            ["test"], source="solution or project file")
    composer = _read_json_manifest(base / "composer.json")
    composer_scripts = composer.get("scripts", {}) if composer else {}
    if isinstance(composer_scripts, dict) and "test" in composer_scripts:
        add("composer-test", "PHP", "Run Composer test script", find("composer"),
            ["test"], source="composer.json:scripts.test")
    if (base / "Gemfile").is_file() and (base / "Rakefile").is_file():
        add("ruby-rake-test", "Ruby", "Run configured Rake tests", find("bundle"),
            ["exec", "rake", "test"], source="Gemfile and Rakefile")

    return {
        "status": "discovery_complete",
        "project_root": str(base),
        "checks_found": len(checks),
        "checks": sorted(checks, key=lambda item: item["check_id"]),
        "commands_executed": False,
        "project_code_executed": False,
        "network_used": False,
        "ai_enabled": False,
    }


def run_project_check(
    check_id: str,
    root: str | Path,
    *,
    execute_project_code: bool = False,
    timeout_seconds: int = 120,
) -> dict:
    """Run one discovered fixed profile only after explicit project-code approval."""
    if execute_project_code is not True:
        raise ProjectCheckError("Project checks execute project code; explicit approval is required")
    if not isinstance(check_id, str) or not check_id:
        raise ProjectCheckError("check_id must be a non-empty string")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 300:
        raise ProjectCheckError("timeout_seconds must be from 1 to 300")
    base = Path(root).expanduser().resolve()
    inventory = discover_project_checks(base)
    selected = next((item for item in inventory["checks"] if item["check_id"] == check_id), None)
    if selected is None:
        raise ProjectCheckError(f"Project check is not available for this project: {check_id}")
    try:
        result = run_process(
            selected["argv"],
            cwd=base,
            allowed_root=base,
            approved_executables=[selected["executable"]],
            timeout_seconds=timeout_seconds,
            output_limit_bytes=64_000,
        )
    except ProcessRunnerError as exc:
        raise ProjectCheckError(str(exc)) from exc
    return {
        "check_id": check_id,
        "description": selected["description"],
        "source": selected["source"],
        "ok": result["exit_code"] == 0 and not result["timed_out"],
        "project_code_execution_approved": True,
        **result,
    }
