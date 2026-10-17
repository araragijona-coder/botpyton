"""Inventory of installed developer tools; detection never executes project code."""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    executable: str
    purpose: str
    probe_args: tuple[str, ...]
    ecosystems: tuple[str, ...]


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("python", "python", "Python runtime", ("--version",), ("Python",)),
    ToolSpec("python3", "python3", "Python runtime (alternate name)", ("--version",), ("Python",)),
    ToolSpec("git", "git", "Version control", ("--version",), ("Git",)),
    ToolSpec("node", "node", "JavaScript runtime", ("--version",), ("JavaScript", "TypeScript")),
    ToolSpec("npm", "npm", "JavaScript package manager", ("--version",), ("JavaScript", "TypeScript")),
    ToolSpec("pnpm", "pnpm", "JavaScript package manager", ("--version",), ("JavaScript", "TypeScript")),
    ToolSpec("yarn", "yarn", "JavaScript package manager", ("--version",), ("JavaScript", "TypeScript")),
    ToolSpec("dotnet", "dotnet", ".NET SDK/runtime", ("--version",), ("C#", "F#")),
    ToolSpec("java", "java", "Java runtime", ("-version",), ("Java", "Kotlin")),
    ToolSpec("javac", "javac", "Java compiler", ("-version",), ("Java",)),
    ToolSpec("go", "go", "Go toolchain", ("version",), ("Go",)),
    ToolSpec("rustc", "rustc", "Rust compiler", ("--version",), ("Rust",)),
    ToolSpec("cargo", "cargo", "Rust package/build tool", ("--version",), ("Rust",)),
    ToolSpec("cmake", "cmake", "C/C++ build configuration", ("--version",), ("C", "C++")),
    ToolSpec("gcc", "gcc", "GNU C/C++ compiler", ("--version",), ("C", "C++")),
    ToolSpec("clang", "clang", "LLVM C/C++ compiler", ("--version",), ("C", "C++")),
    ToolSpec("php", "php", "PHP runtime", ("--version",), ("PHP",)),
    ToolSpec("ruby", "ruby", "Ruby runtime", ("--version",), ("Ruby",)),
    ToolSpec("powershell", "pwsh", "PowerShell runtime", ("-NoLogo", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"), ("PowerShell",)),
)


def discover_tools(*, path_value: str | None = None, specs: Iterable[ToolSpec] = TOOL_SPECS) -> dict:
    """Return available tool paths without invoking them or searching the network."""
    found = []
    seen_paths: set[str] = set()
    for spec in specs:
        executable_path = shutil.which(spec.executable, path=path_value)
        if executable_path is None:
            continue
        normalized = executable_path.casefold()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        found.append({
            **asdict(spec),
            "probe_args": list(spec.probe_args),
            "ecosystems": list(spec.ecosystems),
            "resolved_path": executable_path,
            "probe_executed": False,
        })
    return {
        "status": "discovery_complete",
        "tools_found": len(found),
        "tools": found,
        "network_used": False,
        "ai_enabled": False,
    }
