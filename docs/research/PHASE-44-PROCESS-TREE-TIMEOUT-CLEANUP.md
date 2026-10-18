# Phase 44 — Process-tree termination on timeout

## Baseline
- Repository: `araragijona-coder/botpyton`
- Base `main`: `dac0eb7af1c2a0c962d37e95901d102e1995b191`
- Components: `bot/process_runner.py`, `run_process()`

## Defect
The runner applied timeouts to the direct child process. On POSIX, `process.kill()` only terminated the process represented by the `Popen` instance. If that process spawned a child which inherited stdout/stderr, the child could remain alive after the parent was killed, continue doing work, and keep the capture threads blocked until its own exit. In the opposite direction, the runner could raise a collector error even though its parent had timed out, while descendants remained behind. This undermines task time limits and can leave work running beyond BotPyton's report/queue state.

## Correction
- Start POSIX subprocesses in a new session, so the direct child is the leader of a process group isolated from BOTPYTON and unrelated processes.
- On timeout and execution-cleanup failures, terminate that POSIX process group with `SIGKILL`; retain a direct-process fallback if group signaling is unavailable.
- On Windows, when a Job Object is attached with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, close it at timeout before waiting on output-reader threads. This terminates the process and assigned descendants before pipe-collector joins.
- Keep the fixed executable allowlist, shell-disabled launch, sanitized environment, existing time/output/memory limits, and return schema unchanged.
- Add regression coverage: a parent spawns a child that would write a marker after the timeout. The marker must never appear.

## Official maintenance research
These are analogous lessons about process lifecycle management, not claims that BOTPYTON has the same bug:
- [GitHub Actions Runner issue #4601](https://github.com/actions/runner/issues/4601), opened 3 August 2026, describes orphaned descendant processes missed by a cleanup snapshot and surviving after their parent was killed. It documents why process-tree lifecycle matters in a job runner.
- [GitHub Actions Runner releases](https://github.com/actions/runner/releases) include the v2.336.0 change “Wait for worker to finish during cancel” and worker/session cleanup adjustments, showing cancellation lifecycle has practical failure modes that maintainers address in releases.
- [Python subprocess documentation](https://docs.python.org/3/library/subprocess.html) describes timeout handling for child processes; the robust fix here must account for BOTPYTON's descendants and inherited pipes rather than assuming the direct process and its children are identical.

## Safety scope
No changes to task permissions, GitHub tokens, shell policy, local source modification policy, or PC-care operations. This specifically prevents timed-out tool/test subprocess trees from continuing after BOTPYTON believes execution has ended.

## Validation gate
Run Linux foundation tests and Windows low-memory smoke on the PR candidate. Merge only if both pass. Then verify both workflows on the merged `main` SHA. These automated checks are not a claim that the user's Windows PC was tested.
