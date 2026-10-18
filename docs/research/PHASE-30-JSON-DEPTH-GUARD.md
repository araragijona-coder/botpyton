# Phase 30 — JSON verification must be bounded during decoding

Date: 2026-10-18

## Confirmed code-level gap

The `json_valid` task-verification check previously inspected `path.stat().st_size` and then called `Path.read_text()` followed by `json.loads()`. That design had two gaps:

1. A file could grow or be replaced between the size check and `read_text()`, so the check did not itself guarantee a 200 KB maximum read.
2. A syntactically valid JSON value with excessive nesting could exceed Python's decoder recursion capacity. `RecursionError` was not caught by the verification loop, so it could escape the normal per-check failure result and interrupt verification of the task.

This was a code-path finding from the current implementation; it is not evidence that a user has encountered it in production.

## Changes

- Reuse `read_bounded_bytes()` so the actual read is capped and file identity/size are checked while reading.
- Reuse the iterative `enforce_json_depth()` guard with its default limit of 64 nesting levels.
- Convert bounded-read, decoding-depth and decoder `RecursionError` failures into a failed verification result. The evaluator can continue processing later checks.
- Add regression tests for a 65-level JSON value and a simulated decoder `RecursionError`.
- Document the limits in the task verification guide.

## External engineering references

- Python's official JSON documentation explicitly notes that the module does not impose an application-level maximum nesting depth beyond limitations of Python's relevant types/interpreter: https://docs.python.org/3/library/json.html
- BleachBit's official 6.0.6 maintenance notes published on 2026-10-17 describe fixes to Windows settings persistence and file-wiping behavior. They illustrate why maintenance release notes and regression-driven hardening are useful evidence sources, but those listed bugs are not asserted to be the same defect found in BOTPYTON: https://www.bleachbit.org/news/bleachbit-606
- Existing BOTPYTON bounded input helper and its tests: `bot/bounded_io.py`, `tests/test_bounded_io.py`.

## Validation boundary

CI must pass on the exact pull request commit and again after merge on `main`. This change only strengthens the declarative `json_valid` check; it does not execute JSON content or project code, and it is not a general security certification.
