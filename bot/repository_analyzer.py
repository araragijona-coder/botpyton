"""Bounded, read-only repository inventory. Never imports or executes project code."""
from __future__ import annotations

from pathlib import Path

DEFAULT_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", "dist", "build", "target", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".next", ".nuxt", ".idea",
    ".vscode", "vendor", "packages",
}
MANIFESTS = {
    "pyproject.toml": ("Python", "python -m pytest"),
    "pytest.ini": ("Python", "python -m pytest"),
    "setup.py": ("Python", "python setup.py --help"),
    "requirements.txt": ("Python", "python -m pip check"),
    "Pipfile": ("Python", "pipenv run pytest"),
    "package.json": ("JavaScript/TypeScript", "npm test (review package.json first)"),
    "tsconfig.json": ("TypeScript", "npx tsc --noEmit (if installed)"),
    "Cargo.toml": ("Rust", "cargo test"),
    "go.mod": ("Go", "go test ./..."),
    "pom.xml": ("Java", "mvn test"),
    "build.gradle": ("Java/Kotlin", "./gradlew test (if wrapper exists)"),
    "build.gradle.kts": ("Kotlin/Gradle", "./gradlew test (if wrapper exists)"),
    "gradlew": ("Java/Kotlin", "./gradlew test (review wrapper first)"),
    "composer.json": ("PHP", "composer test (if script exists)"),
    "Gemfile": ("Ruby", "bundle exec rake test (if configured)"),
    "CMakeLists.txt": ("C/C++", "cmake --build (requires configured build directory)"),
    "Makefile": ("Make", "make -n (dry-run only; inspect Makefile first)"),
    "Cargo.lock": ("Rust", "cargo test"),
    "go.work": ("Go", "go test ./..."),
}
EXTENSIONS = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript/React",
    ".ts": "TypeScript", ".tsx": "TypeScript/React", ".mjs": "JavaScript",
    ".c": "C", ".h": "C/C header", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C++",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
    ".svelte": "Svelte", ".sh": "Shell", ".ps1": "PowerShell",
    ".bat": "Windows batch", ".cmd": "Windows batch", ".sql": "SQL",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML",
    ".md": "Markdown",
}


class RepositoryAnalysisError(ValueError):
    """Raised when a repository cannot be safely inventoried."""


def inspect_repository(
    root: str | Path = ".",
    *,
    max_files: int = 5000,
    max_depth: int = 8,
    max_file_bytes: int = 2_000_000,
) -> dict:
    """Inventory names and manifests within strict bounds; never reads source contents."""
    if max_files < 1 or max_depth < 0 or max_file_bytes < 1:
        raise RepositoryAnalysisError("Limits must be positive (max_depth may be zero).")
    path = Path(root).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise RepositoryAnalysisError(f"Repository path is not a directory: {path}")

    counts: dict[str, int] = {}
    manifests: list[dict[str, str]] = []
    candidates: list[str] = []
    sample_files: list[str] = []
    visited = 0
    oversized = 0
    truncated = False
    ignored = set(DEFAULT_IGNORED_DIRS)
    stack: list[tuple[Path, int]] = [(path, 0)]

    while stack:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name.casefold(), reverse=True)
        except (PermissionError, OSError):
            continue
        child_dirs: list[Path] = []
        for entry in entries:
            if entry.is_symlink():
                continue
            try:
                if entry.is_dir():
                    if entry.name not in ignored and not entry.name.startswith(".git"):
                        if depth < max_depth:
                            child_dirs.append(entry)
                        else:
                            truncated = True
                    continue
                if not entry.is_file():
                    continue
                visited += 1
                if visited > max_files:
                    truncated = True
                    break
                relative = entry.relative_to(path).as_posix()
                suffix = entry.suffix.lower()
                language = EXTENSIONS.get(suffix)
                if language:
                    counts[language] = counts.get(language, 0) + 1
                if len(sample_files) < 100:
                    sample_files.append(relative)
                if entry.name in MANIFESTS:
                    ecosystem, command = MANIFESTS[entry.name]
                    manifests.append({"path": relative, "ecosystem": ecosystem, "candidate_check": command})
                    candidates.append(command)
                if entry.name.endswith(".sln") or entry.name.endswith(".csproj"):
                    manifests.append({"path": relative, "ecosystem": "C#/.NET",
                                      "candidate_check": "dotnet test (review solution and SDK first)"})
                    candidates.append("dotnet test (review solution and SDK first)")
                try:
                    if entry.stat().st_size > max_file_bytes:
                        oversized += 1
                except OSError:
                    pass
            except OSError:
                continue
        if visited > max_files:
            break
        stack.extend((child, depth + 1) for child in reversed(child_dirs))

    return {
        "status": "inventory_complete_with_limits" if truncated else "inventory_complete",
        "root": str(path),
        "files_seen": min(visited, max_files),
        "max_files": max_files,
        "max_depth": max_depth,
        "oversized_files": oversized,
        "truncated": truncated,
        "language_counts": dict(sorted(counts.items())),
        "manifests": sorted(manifests, key=lambda item: item["path"]),
        "candidate_checks_not_executed": sorted(set(candidates)),
        "sample_files": sorted(sample_files),
        "ai_enabled": False,
        "project_code_executed": False,
        "network_used": False,
    }
