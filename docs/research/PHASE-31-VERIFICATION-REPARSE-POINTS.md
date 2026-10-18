# Phase 31 — Reject Windows reparse points in task verification paths

Date: 2026-10-18

## Confirmed implementation gap

The task-verification path resolver in `bot/verification.py::_target` rejected symbolic links with `Path.is_symlink()` and checked the final resolved path against the project root. On Windows, directory junctions and other filesystem reparse points are not all represented by `is_symlink()`. A junction whose resolved target remains inside the project root could therefore be treated as a normal path alias by the verification resolver.

This is a narrow path-validation gap in a read-only checker; it is not evidence that an arbitrary file was modified or that a user encountered an incident. The local mutation path already used BOTPYTON's shared link/reparse-point detector.

## Changes applied in the candidate

- Use the same link/reparse-point detector in task verification that BOTPYTON already uses in file-change safety checks.
- Inspect each existing path component with `lstat()` before checking it, so missing paths continue to produce the expected ordinary "file does not exist" result instead of being mistaken for a link.
- Fail a verification check explicitly when an existing component is a symbolic link or reparse point, including an internal junction-like alias.
- Add cross-platform regression coverage by simulating a Windows reparse-point detector result, plus a test that missing paths still behave normally.
- Update the task-verification guide.

## External references

- Microsoft Learn, *Reparse points*: reparse points are filesystem metadata used by mechanisms including NTFS links and mounted folders: https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points
- Microsoft Learn, *Reparse points and file operations*: developers must account for special filesystem behavior when opening and manipulating paths: https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points-and-file-operations
- Python `pathlib` documentation: `Path.is_junction()` was added in Python 3.12; BOTPYTON supports Python 3.11+, so this implementation reuses the existing attribute-based detector rather than depending on a 3.12-only method: https://docs.python.org/3.12/library/pathlib.html
- BleachBit 6.0.0 official release notes document a fix to avoid following directory junctions/symlinks in the Recycle Bin because that could cause unintended data loss: https://www.bleachbit.org/news/bleachbit-600
- BleachBit 6.0.6 official maintenance notes (2026-10-17): https://www.bleachbit.org/news/bleachbit-606

These references motivate defensive path handling; the upstream defects are not claimed to be the same bug as the code-level gap found here.

## Limits and required verification

Rejecting detected reparse points is one layer of path validation and does not eliminate every possible TOCTOU race with a process that swaps filesystem objects concurrently. CI must pass on the exact PR head and again on `main` after merge. This phase does not change any file or execute project code as part of its verification operations.
