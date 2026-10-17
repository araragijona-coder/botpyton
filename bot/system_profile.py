"""Low-cost host resource profile and conservative child-process memory budget."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path


def _available_memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        except (AttributeError, OSError, ImportError):
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text(encoding="ascii").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return None


def recommended_child_memory_limit_mb(available_bytes: int | None = None) -> int:
    """Reserve at least half of currently available RAM for the rest of the system."""
    if available_bytes is None:
        available_bytes = _available_memory_bytes()
    if available_bytes is None or available_bytes <= 0:
        return 256
    available_mb = available_bytes // (1024 * 1024)
    if available_mb < 128:
        return 64
    return max(128, min(768, available_mb // 2))


def get_system_profile(path: str | Path = ".") -> dict:
    target = Path(path).expanduser().resolve()
    memory_bytes = _available_memory_bytes()
    try:
        disk = shutil.disk_usage(target if target.exists() else target.parent)
        disk_free = int(disk.free)
    except OSError:
        disk_free = None
    return {
        "os": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "available_memory_bytes": memory_bytes,
        "available_memory_mb": None if memory_bytes is None else memory_bytes // (1024 * 1024),
        "recommended_child_memory_limit_mb": recommended_child_memory_limit_mb(memory_bytes),
        "project_path": str(target),
        "disk_free_bytes": disk_free,
        "disk_free_mb": None if disk_free is None else disk_free // (1024 * 1024),
        "ai_enabled": False,
        "network_used": False,
    }
