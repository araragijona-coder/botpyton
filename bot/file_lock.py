"""Cross-platform advisory file locking for short local state transactions."""
from __future__ import annotations

import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_STATE = threading.local()
_REPARSE_POINT_ATTRIBUTE = 0x400


def _thread_lock_for(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE)


@contextmanager
def exclusive_file_lock(path: str | Path) -> Iterator[None]:
    """Serialize threads and processes using a stable sidecar lock file.

    The sidecar is intentionally not removed after use: deleting a lock file while
    another process has it open can split later callers across different inodes.
    The OS releases its advisory lock if a process exits unexpectedly.
    """
    lock_path = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(os.path.normpath(str(lock_path)))
    thread_lock = _thread_lock_for(key)

    with thread_lock:
        depths = getattr(_THREAD_STATE, "depths", None)
        if depths is None:
            depths = {}
            _THREAD_STATE.depths = depths
        if key in depths:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return

        descriptor = None
        acquired = False
        try:
            flags = os.O_CREAT | os.O_RDWR
            flags |= getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            current = os.stat(lock_path, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or _is_reparse(current)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise OSError("State lock must be a stable regular file, not a link/reparse point")

            if os.name == "nt":
                import msvcrt

                # Windows byte-range locks require the locked byte to exist on
                # older runtimes/filesystems; concurrent initialization writes
                # the same single NUL byte before anyone acquires the lock.
                if opened.st_size == 0:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            acquired = True
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
        finally:
            if descriptor is not None:
                if acquired:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
                else:
                    os.close(descriptor)
