# Windows Job Object close-failure handling

## Context

The original work was developed at commit `19e7b3408d42f7cb2c243b456bf56efa78b14e5e`. An exhaustive branch audit on 2026-10-23 found that the commit's specific Windows cleanup improvement was not present in the current mainline. The old branch also predates later process-runner hardening, so only the targeted fix is being re-applied on the current baseline.

## Failure behavior

Windows Job Objects are configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The process-tree cleanup previously called `CloseHandle` without inspecting its Boolean result, then reported cleanup ownership as complete. If the close failed, descendants might remain alive while the caller skipped the final handle-close attempt.

The corrected logic:

1. Returns success only if `CloseHandle` succeeds.
2. If close fails, calls `TerminateJobObject(handle, 1)` to explicitly stop members of the job.
3. If job termination is unavailable or fails, attempts to kill the direct process.
4. Returns `False` after a failed initial close so the existing caller retains responsibility for final handle cleanup.
5. Declares the `TerminateJobObject` Win32 argument and return types.

## Regression coverage

Tests use fake process objects and mocked kernel32 calls. They cover both (a) CloseHandle failure followed by successful job termination and (b) failure of both operations followed by direct-process fallback. They do not terminate real processes and do not require Windows privileges on the test runner.

## Scope and release gate

The fix does not broaden the executable allowlist, change argv/shell behavior, install software, change project authorization, or add a network client. A fresh Linux foundation workflow and Windows low-memory smoke workflow must both pass on the current-main integration branch before this change is merged. The old source-branch CI is historical evidence only; it is not a substitute for validating this backport. No claim is made that the user's PC was tested.
