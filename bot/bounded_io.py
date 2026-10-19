"""Small bounded-read helpers for local JSON and diagnostic inputs."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

DEFAULT_JSON_MAX_DEPTH = 64


class BoundedReadError(ValueError):
    """Raised when a local input is too large or changes during a bounded read."""


def _signature(info: os.stat_result) -> tuple[int, int, int]:
    # Compare stable file identity and size only. Windows stat/fstat timestamp
    # representations can differ despite referring to the same unchanged file.
    return (info.st_dev, info.st_ino, info.st_size)


def _open_binary_reader(path: Path, *, label: str):
    """Open a bounded-read input without following a Windows reparse-point leaf.

    On Windows the handle is opened with FILE_FLAG_OPEN_REPARSE_POINT and a
    conservative share mode. This pins the leaf object for the duration of
    the read and refuses a leaf that is itself a symlink/reparse point.
    Parent path components still use normal Win32 path resolution, so callers
    must retain their own path-chain checks for security-sensitive roots.
    """
    if os.name != "nt":
        return path.open("rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("volume_serial", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetFileInformationByHandle.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    generic_read = 0x80000000
    share_read_only = 0x00000001
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_sequential_scan = 0x08000000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(
        os.fspath(path), generic_read, share_read_only, None, open_existing,
        file_flag_open_reparse_point | file_flag_sequential_scan, None,
    )
    if handle is None or handle == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), os.fspath(path))

    try:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), os.fspath(path))
        if info.attributes & file_attribute_reparse_point:
            raise BoundedReadError(f"{label} is a symbolic link or Windows reparse point")
        if info.attributes & file_attribute_directory:
            raise BoundedReadError(f"{label} must be a regular file")

        # Ownership transfers to the CRT descriptor after open_osfhandle succeeds.
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        return os.fdopen(descriptor, "rb")
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)


def read_bounded_bytes(path: str | Path, *, limit_bytes: int, label: str = "Input file") -> bytes:
    """Read at most limit_bytes + 1 bytes, even if a file grows after stat()."""
    target = Path(path)
    if not isinstance(limit_bytes, int) or isinstance(limit_bytes, bool) or limit_bytes < 0:
        raise ValueError("limit_bytes must be a non-negative integer")
    try:
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            raise BoundedReadError(f"{label} must be a regular file")
        if before.st_size < 0 or before.st_size > limit_bytes:
            raise BoundedReadError(f"{label} exceeds {limit_bytes} bytes")
        with _open_binary_reader(target, label=label) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
                raise BoundedReadError(f"{label} changed while it was being opened")
            raw = stream.read(limit_bytes + 1)
            after_open = os.fstat(stream.fileno())
        if len(raw) > limit_bytes or after_open.st_size > limit_bytes:
            raise BoundedReadError(f"{label} exceeds {limit_bytes} bytes")
        after_path = target.stat()
        if _signature(after_open) != _signature(opened) or _signature(after_path) != _signature(after_open):
            raise BoundedReadError(f"{label} changed during bounded reading")
        return raw
    except BoundedReadError:
        raise
    except OSError as exc:
        raise BoundedReadError(f"Cannot read {label}: {exc}") from exc


def enforce_json_depth(value: Any, *, max_depth: int = DEFAULT_JSON_MAX_DEPTH, label: str = "JSON input") -> None:
    """Reject deeply nested decoded JSON without recursive traversal or a large work stack."""
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    stack: list[tuple[Any, int]] = [(iter((value,)), 0)]
    while stack:
        children, parent_depth = stack[-1]
        try:
            current = next(children)
        except StopIteration:
            stack.pop()
            continue
        depth = parent_depth + 1
        if depth > max_depth:
            raise BoundedReadError(f"{label} exceeds the JSON nesting depth limit ({max_depth})")
        if isinstance(current, dict):
            stack.append((iter(current.values()), depth))
        elif isinstance(current, list):
            stack.append((iter(current), depth))
