"""Deterministic, read-only project toolchain matrix; never runs project code."""
from __future__ import annotations

import fnmatch
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

from .project_checks import discover_project_checks
from .tool_registry import discover_tools

MAX_SCAN_ENTRIES = 2_000
MAX_SCAN_DEPTH = 5
MAX_EVIDENCE_PATHS = 20
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "vendor", "dist", "build", "target", "bin", "obj", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".tox", ".idea", ".vscode",
    ".next", ".nuxt", ".gradle", ".godot", "Library", "Temp",
})


class ToolchainMatrixError(ValueError):
    """Raised when a project root or injected inventory is invalid."""


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    display_name: str
    manifest_names: tuple[str, ...] = ()
    manifest_patterns: tuple[str, ...] = ()
    path_markers: tuple[str, ...] = ()
    directory_markers: tuple[str, ...] = ()
    source_extensions: tuple[str, ...] = ()
    required_tool_groups: tuple[tuple[str, ...], ...] = ()
    optional_tool_ids: tuple[str, ...] = ()
    candidate_check_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


ADAPTERS: tuple[AdapterSpec, ...] = (
    AdapterSpec(
        "python", "Python",
        manifest_names=("pyproject.toml", "requirements.txt", "requirements-dev.txt",
                        "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "pipfile"),
        manifest_patterns=("requirements-*.txt",),
        source_extensions=(".py", ".pyi"),
        required_tool_groups=(("python", "python3", "py"),),
        optional_tool_ids=("pytest",),
        candidate_check_ids=("python-unittest",),
        notes=("Syntax inspection is not proof of runtime correctness.",),
    ),
    AdapterSpec(
        "javascript-typescript", "JavaScript / TypeScript",
        manifest_names=("package.json", "tsconfig.json", "deno.json", "deno.jsonc"),
        manifest_patterns=("package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                           "bun.lock", "bun.lockb"),
        source_extensions=(".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
        required_tool_groups=(("node", "deno", "bun"),),
        optional_tool_ids=("npm", "pnpm", "yarn", "deno", "bun"),
        candidate_check_ids=("npm-test", "npm-build"),
        notes=("Scripts discovered in package manifests are not executed during detection.",),
    ),
    AdapterSpec(
        "dotnet", "C# / F# / .NET",
        manifest_names=("global.json",),
        manifest_patterns=("*.sln", "*.slnx", "*.csproj", "*.fsproj", "*.vbproj"),
        source_extensions=(".cs", ".fs", ".vb"),
        required_tool_groups=(("dotnet",),),
        candidate_check_ids=("dotnet-test",),
        notes=("A detected SDK does not prove that all project workloads are installed.",),
    ),
    AdapterSpec(
        "java-kotlin", "Java / Kotlin",
        manifest_names=("pom.xml", "build.gradle", "build.gradle.kts",
                        "settings.gradle", "settings.gradle.kts", "gradlew",
                        "gradlew.bat", "gradle.properties"),
        source_extensions=(".java", ".kt", ".kts"),
        required_tool_groups=(("java",),),
        optional_tool_ids=("javac", "mvn", "gradle", "kotlinc"),
        candidate_check_ids=("maven-test", "gradle-test"),
        notes=("Gradle wrapper files are identified but never invoked automatically.",),
    ),
    AdapterSpec(
        "android", "Android",
        manifest_names=("androidmanifest.xml",),
        path_markers=("app/src/main/androidmanifest.xml", "src/main/androidmanifest.xml"),
        required_tool_groups=(("java",),),
        optional_tool_ids=("gradle",),
        candidate_check_ids=("gradle-test",),
        notes=("Android build-tools, SDK components and emulator availability are not inferred from a project marker.",),
    ),
    AdapterSpec(
        "rust", "Rust",
        manifest_names=("cargo.toml",),
        source_extensions=(".rs",),
        required_tool_groups=(("cargo",),),
        optional_tool_ids=("rustc",),
        candidate_check_ids=("cargo-test",),
    ),
    AdapterSpec(
        "go", "Go",
        manifest_names=("go.mod",),
        source_extensions=(".go",),
        required_tool_groups=(("go",),),
        candidate_check_ids=("go-test",),
    ),
    AdapterSpec(
        "c-cpp", "C / C++",
        manifest_names=("cmakelists.txt", "meson.build", "makefile"),
        manifest_patterns=("*.vcxproj",),
        source_extensions=(".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
        optional_tool_ids=("cmake", "gcc", "clang"),
        notes=("No generic compile profile is selected; MSVC and project-specific build steps may need explicit configuration.",),
    ),
    AdapterSpec(
        "php", "PHP",
        manifest_names=("composer.json", "composer.lock", "phpunit.xml", "phpunit.xml.dist"),
        source_extensions=(".php",),
        required_tool_groups=(("php",),),
        optional_tool_ids=("composer",),
        candidate_check_ids=("composer-test",),
    ),
    AdapterSpec(
        "ruby", "Ruby",
        manifest_names=("gemfile", "gemfile.lock", "rakefile"),
        source_extensions=(".rb",),
        required_tool_groups=(("ruby",),),
        optional_tool_ids=("bundle",),
        candidate_check_ids=("ruby-rake-test",),
    ),
    AdapterSpec(
        "powershell", "PowerShell",
        source_extensions=(".ps1", ".psm1", ".psd1"),
        required_tool_groups=(("powershell",),),
        notes=("PowerShell scripts are inventoried only; detection never invokes a script.",),
    ),
    AdapterSpec(
        "godot", "Godot",
        manifest_names=("project.godot",),
        source_extensions=(".gd", ".tscn", ".tres"),
        required_tool_groups=(("godot",),),
        notes=("Project inventory does not launch the editor or run the game; a dedicated approved profile is still required.",),
    ),
    AdapterSpec(
        "blender", "Blender",
        source_extensions=(".blend",),
        required_tool_groups=(("blender",),),
        notes=("A .blend filename is a project signal, not a validation of the file or scene.",),
    ),
)

_SKIP_DIRS_CASEFOLD = frozenset(name.casefold() for name in SKIP_DIRS)


def _validate_root(root: str | os.PathLike[str]) -> Path:
    if not isinstance(root, (str, os.PathLike)):
        raise ToolchainMatrixError("project root must be a filesystem path")
    try:
        base = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ToolchainMatrixError("project root cannot be resolved") from exc
    if not base.is_dir():
        raise ToolchainMatrixError("project root must be an existing directory")
    return base


def _is_junction(path: str) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except (OSError, ValueError):
        return True


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _matches_directory_identity(info: os.stat_result, expected: tuple[int, int]) -> bool:
    return stat.S_ISDIR(info.st_mode) and _directory_identity(info) == expected


def _supports_handle_anchored_scan() -> bool:
    return (
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in getattr(os, "supports_dir_fd", set())
    )


@contextmanager
def _scandir_project_directory(
    base: Path,
    current: Path,
    expected_root_identity: tuple[int, int],
    expected_identity: tuple[int, int],
) -> Iterator[os.ScandirIterator[str]]:
    """Enumerate a directory without following replaced path components where supported."""
    if _supports_handle_anchored_scan():
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(os.sep, flags)
        try:
            for component in base.parts[1:]:
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            if not _matches_directory_identity(os.fstat(fd), expected_root_identity):
                raise OSError("project root changed during inventory")

            for component in current.relative_to(base).parts:
                if component in ("", "."):
                    continue
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            if not _matches_directory_identity(os.fstat(fd), expected_identity):
                raise OSError("directory changed before inventory")

            with os.scandir(fd) as iterator:
                yield iterator
        finally:
            os.close(fd)
        return

    # Windows fallback: verify the directory identity before and after enumeration.
    # If the path changes while it is open, discard that directory's collected entries.
    before = os.stat(current, follow_symlinks=False)
    if (
        not _matches_directory_identity(before, expected_identity)
        or current.is_symlink()
        or _is_junction(str(current))
    ):
        raise OSError("directory changed or became a link before inventory")
    try:
        with os.scandir(current) as iterator:
            yield iterator
    finally:
        after = os.stat(current, follow_symlinks=False)
        if (
            not _matches_directory_identity(after, expected_identity)
            or current.is_symlink()
            or _is_junction(str(current))
        ):
            raise OSError("directory changed or became a link during inventory")


def _scan_project(base: Path) -> tuple[list[str], list[str], dict]:
    files: list[str] = []
    directories: list[str] = []
    scan = {
        "entries_examined": 0,
        "entry_limit": MAX_SCAN_ENTRIES,
        "depth_limit": MAX_SCAN_DEPTH,
        "files_seen": 0,
        "directories_seen": 0,
        "link_or_junctions_skipped": 0,
        "directories_unreadable": 0,
        "scan_truncated": False,
        "depth_limit_reached": False,
    }
    try:
        root_stat = os.stat(base, follow_symlinks=False)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise OSError("project root is no longer a directory")
    except OSError as exc:
        raise ToolchainMatrixError("project root changed or cannot be safely inventoried") from exc
    root_identity = _directory_identity(root_stat)
    pending: list[tuple[Path, int, tuple[int, int]]] = [(base, 0, root_identity)]

    while pending:
        current, depth, expected_identity = pending.pop()
        remaining = MAX_SCAN_ENTRIES - scan["entries_examined"]
        if remaining <= 0:
            scan["scan_truncated"] = True
            break

        entries: list[tuple[str, str, tuple[int, int] | None]] = []
        overflow = False
        try:
            with _scandir_project_directory(base, current, root_identity, expected_identity) as iterator:
                for entry in iterator:
                    if len(entries) >= remaining:
                        overflow = True
                        break
                    entry_path = current / entry.name
                    try:
                        if entry.is_symlink() or _is_junction(str(entry_path)):
                            kind, identity = "link", None
                        else:
                            entry_stat = entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(entry_stat.st_mode):
                                kind, identity = "directory", _directory_identity(entry_stat)
                            elif stat.S_ISREG(entry_stat.st_mode):
                                kind, identity = "file", None
                            else:
                                kind, identity = "other", None
                    except (OSError, RuntimeError, ValueError):
                        kind, identity = "unreadable", None
                    entries.append((entry.name, kind, identity))
        except OSError:
            scan["directories_unreadable"] += 1
            continue

        if overflow:
            # Do not classify a directory when its inventory is incomplete.
            scan["entries_examined"] += len(entries)
            scan["scan_truncated"] = True
            break

        entries.sort(key=lambda entry: (entry[0].casefold(), entry[0]))
        scan["entries_examined"] += len(entries)
        child_directories: list[tuple[Path, int, tuple[int, int]]] = []

        for name, kind, identity in entries:
            try:
                entry_path = current / name
                if kind == "link":
                    scan["link_or_junctions_skipped"] += 1
                    continue
                if kind == "unreadable":
                    scan["directories_unreadable"] += 1
                    continue
                rel = entry_path.relative_to(base).as_posix()
                if kind == "directory" and identity is not None:
                    directories.append(rel)
                    scan["directories_seen"] += 1
                    if name.casefold() in _SKIP_DIRS_CASEFOLD:
                        continue
                    if depth >= MAX_SCAN_DEPTH:
                        scan["depth_limit_reached"] = True
                        scan["scan_truncated"] = True
                        continue
                    child_directories.append((entry_path, depth + 1, identity))
                elif kind == "file":
                    files.append(rel)
                    scan["files_seen"] += 1
                else:
                    scan["link_or_junctions_skipped"] += 1
            except (OSError, RuntimeError, ValueError):
                scan["directories_unreadable"] += 1

        # Reverse insertion makes the next traversal lexicographically ascending.
        pending.extend(reversed(child_directories))

    files.sort(key=lambda item: (item.casefold(), item))
    directories.sort(key=lambda item: (item.casefold(), item))
    return files, directories, scan


def _matches(adapter: AdapterSpec, files: list[str], directories: list[str]) -> tuple[list[str], list[str]]:
    strong: list[str] = []
    source: list[str] = []
    manifest_names = {name.casefold() for name in adapter.manifest_names}
    path_markers = {name.casefold() for name in adapter.path_markers}
    directory_markers = {name.casefold() for name in adapter.directory_markers}
    extensions = {suffix.casefold() for suffix in adapter.source_extensions}

    for path in files:
        folded = path.casefold()
        name = PurePosixPath(folded).name
        if name in manifest_names or folded in path_markers or any(
            fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in adapter.manifest_patterns
        ):
            strong.append(path)
        elif PurePosixPath(folded).suffix in extensions:
            source.append(path)
    for path in directories:
        if path.casefold() in directory_markers:
            strong.append(path + "/")
    return strong, source


def inspect_toolchain_matrix(
    root: str | os.PathLike[str] = ".",
    *,
    available_tool_ids: Iterable[str] | None = None,
    available_check_ids: Iterable[str] | None = None,
) -> dict:
    """Return a bounded inventory; never probes executables or runs project code."""
    base = _validate_root(root)
    files, directories, scan = _scan_project(base)

    if available_tool_ids is None:
        detected = discover_tools()
        tool_ids = {
            item["tool_id"] for item in detected.get("tools", [])
            if isinstance(item, dict) and isinstance(item.get("tool_id"), str)
        }
        tool_inventory_source = "registered_executables_on_path"
    else:
        if isinstance(available_tool_ids, (str, bytes)):
            raise ToolchainMatrixError("available_tool_ids must be an iterable of identifiers, not text")
        tool_ids = {
            value for value in available_tool_ids
            if isinstance(value, str) and value and len(value) <= 80
        }
        tool_inventory_source = "injected_test_inventory"

    if available_check_ids is None:
        try:
            check_result = discover_project_checks(base)
            check_ids = {
                item["check_id"] for item in check_result.get("checks", [])
                if isinstance(item, dict) and isinstance(item.get("check_id"), str)
            }
            check_inventory_status = "discovered_at_project_root"
        except (OSError, ValueError, RuntimeError, RecursionError) as exc:
            check_ids = set()
            check_inventory_status = "unavailable_" + type(exc).__name__
    else:
        if isinstance(available_check_ids, (str, bytes)):
            raise ToolchainMatrixError("available_check_ids must be an iterable of identifiers, not text")
        check_ids = {
            value for value in available_check_ids
            if isinstance(value, str) and value and len(value) <= 100
        }
        check_inventory_status = "injected_test_inventory"

    adapters = []
    for adapter in ADAPTERS:
        strong, source = _matches(adapter, files, directories)
        if not strong and not source:
            continue

        missing_groups = [
            list(group) for group in adapter.required_tool_groups
            if not any(tool_id in tool_ids for tool_id in group)
        ]
        available_required_groups = [
            sorted(tool_id for tool_id in group if tool_id in tool_ids)
            for group in adapter.required_tool_groups
            if any(tool_id in tool_ids for tool_id in group)
        ]
        optional_available = sorted(
            tool_id for tool_id in adapter.optional_tool_ids if tool_id in tool_ids
        )
        # Check discovery is already scoped to the selected root. Do not require a
        # second root-level marker: test files and ecosystem markers can be nested.
        profiles_available = sorted(
            check_id for check_id in adapter.candidate_check_ids
            if check_id in check_ids
        )
        profiles_not_available = sorted(
            check_id for check_id in adapter.candidate_check_ids
            if check_id not in profiles_available
        )
        if missing_groups:
            adapter_status = "required_tool_missing"
        elif profiles_available:
            adapter_status = "known_profile_available"
        else:
            adapter_status = "detected_no_matching_builtin_profile"

        evidence_paths = sorted(set(strong + source), key=lambda item: (item.casefold(), item))
        adapters.append({
            "adapter_id": adapter.adapter_id,
            "display_name": adapter.display_name,
            "status": adapter_status,
            "evidence_type": "manifest_or_project_marker" if strong else "source_or_artifact_extension_only",
            "evidence_paths": evidence_paths[:MAX_EVIDENCE_PATHS],
            "evidence_path_count": len(evidence_paths),
            "evidence_truncated": len(evidence_paths) > MAX_EVIDENCE_PATHS,
            "required_tool_groups": [list(group) for group in adapter.required_tool_groups],
            "available_required_tool_groups": available_required_groups,
            "missing_required_tool_groups": missing_groups,
            "optional_tools_available": optional_available,
            "candidate_check_ids": list(adapter.candidate_check_ids),
            "available_check_ids": profiles_available,
            "checks_not_available_or_not_discovered": profiles_not_available,
            "check_discovery_scope": "project_root_only",
            "notes": list(adapter.notes),
        })

    adapters.sort(key=lambda item: item["adapter_id"])
    partial = (
        scan["scan_truncated"]
        or scan["directories_unreadable"] > 0
        or check_inventory_status.startswith("unavailable_")
    )
    return {
        "schema_version": 1,
        "status": "partial" if partial else "discovery_complete",
        "project_root": str(base),
        "scan": scan,
        "tool_inventory": {
            "source": tool_inventory_source,
            "available_tool_ids": sorted(tool_ids),
            "probe_executed": False,
        },
        "check_inventory_status": check_inventory_status,
        "adapters_detected": len(adapters),
        "adapters": adapters,
        "commands_executed": False,
        "project_code_executed": False,
        "files_modified": False,
        "network_used": False,
        "ai_enabled": False,
        "external_ai_apis_enabled": False,
        "trust_limits": [
            "Tool detection is limited to registered executables resolvable on PATH; application-specific install locations may be missed.",
            "A detected project marker does not prove the project builds, runs, or is compatible with the installed tools.",
            "Check profiles are discovered at the selected project root only; nested subprojects are inventory evidence, not execution authorization.",
            "No tool version probe, package installation, build, test, GUI launch, or network request is performed by this command.",
            "An approved project check may execute repository-owned code and may access the network; the process runner is not a network sandbox.",
        ],
    }
