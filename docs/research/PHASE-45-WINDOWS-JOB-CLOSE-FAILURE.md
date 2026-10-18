# Phase 45 — Windows Job Object close-failure handling

## Baseline

- Repository: `araragijona-coder/botpyton`
- Base `main`: `dd1652d00d45c0d6e73e1cd5ec099ee09244e7b4`
- Component: `bot/process_runner.py`, `_terminate_process_tree()`

## Failure path

Windows Job Objects are used to cap child memory and to terminate assigned descendants when the job handle closes. The close API returns a success/failure value. The previous runner ignored that value and unconditionally treated the job as closed. If the close operation failed, the code could proceed as though process-tree cleanup had succeeded without having evidence that the descendants were terminated.

## Correction

- Check the boolean result of `CloseHandle`.
- When it fails, call `TerminateJobObject` explicitly to stop the processes assigned to that job.
- If the job-level termination call also fails, fall back to killing the direct process, preserving a best-effort cleanup path rather than claiming that the tree was terminated.
- Bind the `TerminateJobObject` Win32 signature explicitly.
- Add tests with mocked Win32 calls covering successful job termination after close failure and failure of both job operations.

## Safety and verification

The tests use fake process objects and mocked Win32 API functions; they do not terminate real processes. The change does not alter executable allowlists, process arguments, shell settings, resource budgets, queue permissions, or PC cleanup actions.

Required validation is the Linux foundation test workflow and Windows low-memory smoke workflow on the branch commit. Post-merge workflows would be required before treating the change as validated on `main`; no claim is made that the user's PC was tested.
