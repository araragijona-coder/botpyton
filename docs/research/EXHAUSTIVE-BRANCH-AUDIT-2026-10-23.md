# Exhaustive branch audit — 2026-10-23

## Scope and baseline

Read-only inventory of all remote branches and pull requests in `araragijona-coder/botpyton`. The baseline `main` SHA at audit start was `9cfe171c0c08b91a086b3c0b661ec7666ea0b3b8` (Phase 79 merge).

- Remote branches: **96**, including `main`.
- Pull requests returned by the all-state query: **53**.
- Open or unmerged PRs: **0**. All 53 PRs have a merge timestamp.
- For 53 branch refs, the current branch head SHA exactly matched the head SHA recorded on its corresponding merged PR. These are merged work items, not new candidates to reapply.
- The other **42 branches** had no exact PR-head match and were classified by comparing their tips to current `main` and, where they diverged, reviewing the relevant file differences.

The comparison uses both ancestry counts and content differences. A positive `ahead_by` value by itself does not mean a branch contains a safe new patch: old branch histories can diverge after squash merges, and their tree may lack newer mainline safeguards.

## Findings by branch group

### Historical branches with no unique commits relative to main

The following branch tips are behind `main` with `ahead_by = 0`; no additional commit needs to be integrated from them:

- `bot-bootstrap/phase-01-foundation`
- `bot-bootstrap/phase-02-persistent-queue`
- `bot-bootstrap/phase-03-deterministic-executor`
- `bot-bootstrap/phase-04-persistent-checkpoint`
- `bot-bootstrap/phase-05-known-fixes`
- `bot-bootstrap/phase-06-github-operations`
- `bot-bootstrap/phase-07-autonomous-repair-cycle`
- `bot-bootstrap/phase-08-github-task-orchestration`
- `bot-bootstrap/phase-09-i01-design-a-simulation-candidate`
- `bot-bootstrap/phase-09-i01-full-plan-candidate`
- `bot-bootstrap/phase-09-i01-privileged-pr-isolation`
- `bot-bootstrap/phase-10-deterministic-project-factory`
- `bot-bootstrap/phase-11-educational-project-library`
- `bot-bootstrap/phase-46-local-agent-permissions`
- `bot-bootstrap/phase-47-unknown-error-recovery`
- `bot-bootstrap/phase-48-local-path-race-hardening`
- `bot-bootstrap/phase-49-windows-handle-access`
- `bot-bootstrap/phase-50-windows-write-directory-hardening`
- `bot-bootstrap/phase-51-atomic-file-publication`
- `bot-bootstrap/phase-52-concurrent-write-conflicts`
- `bot-bootstrap/phase-53-external-publication-guard`
- `bot-bootstrap/phase-54-safe-post-publish-recovery`
- `bot-bootstrap/phase-55-atomic-audit-publication`
- `bot-bootstrap/phase-56-audit-cleanup-disclosure`
- `bot-bootstrap/phase-57-prepublication-cleanup-disclosure`
- `bot-bootstrap/phase-58-worker-trust-boundary`
- `bot-bootstrap/phase-59-offline-no-ai`
- `bot-bootstrap/phase-60-network-boundary-audit`
- `bot-bootstrap/phase-61-process-boundary-audit`
- `bot-bootstrap/phase-62-write-path-recovery-audit`
- `bot-bootstrap/phase-63-transaction-journal-recovery`
- `bot-bootstrap/phase-64-journal-trust-boundary`
- `bot-bootstrap/phase-65-cross-platform-path-safety`
- `bot-bootstrap/phase-66-end-to-end-order-flow`
- `bot-bootstrap/phase-67-github-readiness-preflight`
- `bot-bootstrap/phase-68-github-publication-recovery-tests`
- `bot-bootstrap/phase-69-github-publication-reconciliation`
- `bot-bootstrap/phase-70-quality-hardening`
- `bot-bootstrap/phase-70-toolchain-matrix-adapters`

### Diverged branch: phase 33 — do not merge as-is

Branch: `bot-bootstrap/phase-33-bounded-restore-manifest`  
Tip: `41639c2951dfee36573b8220afd2845c4972a016`

Git reports two branch-only commits, but the branch's tree is an older variant of `bot/application_cache_quarantine.py` and its tests. The content comparison showed that the candidate branch lacks safeguards and regressions already present on `main`, including manifest display-metadata validation, failure/reconciliation handling around manifest publication, and several persistence/nesting regression tests. It is not a safe whole-branch merge or cherry-pick candidate. Preserve the newer mainline implementation.

### Functional fix missing from main: phase 45 — isolated backport

Original branch: `bot-bootstrap/phase-45-windows-job-close-failure`  
Original commit: `19e7b3408d42f7cb2c243b456bf56efa78b14e5e`

The change is not represented in `main`: `_terminate_process_tree()` ignored the return value of Windows `CloseHandle`, then treated the process tree as cleaned up. The old branch adds a checked close, explicit `TerminateJobObject` fallback, and leader-process fallback if job termination also fails, with mocked API tests.

The isolated implementation notes and regression coverage are documented in [the Phase 45 backport note](PHASE-45-WINDOWS-JOB-CLOSE-FAILURE.md). The old branch had successful Linux foundation and Windows smoke checks at its own SHA, but it also predates later mainline changes in the same files. Therefore the branch is **not** merged wholesale. Only the targeted cleanup fix and its regression tests are being carried onto a new branch based on the audited `main` baseline. The new candidate requires fresh Linux and Windows CI before integration.

### Diverged branch with AI integration: explicitly excluded

Branch: `feat/phase-28-bounded-ai-repair-cycle`  
Tip: `be9c165f1aa7af1051d1acd877e95abf292a0461`

The branch has three branch-only commits affecting `bot/architect_ai.py`, `bot/architect_auto_patch.py`, and `bot/patch_cycle.py`. It introduces an AI-assisted/auto-patch path contrary to the current requirement that BotPyton remain deterministic and not call AI services. Do not merge or cherry-pick it into the current project. Its existence is not authorization to restore remote-model integration.

## Disposition

1. Keep all 53 already-merged PRs as historical evidence; do not re-apply them.
2. Keep branches with zero unique commits as historical references, not pending work.
3. Do not merge Phase 33 as-is because its implementation and tests are superseded by newer safeguards on `main`.
4. Exclude the AI-repair branch due to the project's no-AI execution boundary.
5. Revalidate the isolated Phase 45 process-tree fix against the current mainline and merge only if both Linux and Windows CI pass.

This audit does not claim the user PC was tested. The branch and CI conclusions apply to repository evidence only.

## Final disposition — Phase 45 backport merged

The Phase 45 correction was carried onto current `main` and tested as PR [#54](https://github.com/araragijona-coder/botpyton/pull/54). The branch SHA `c032b285c58082ba07c1002ac3776f8adb45ace4` passed both checks before merge:

- Linux foundation tests: [run 40049115077](https://github.com/araragijona-coder/botpyton/actions/runs/40049115077) — success.
- Windows low-memory smoke: [run 40049115058](https://github.com/araragijona-coder/botpyton/actions/runs/40049115058) — success.

PR #54 was merged at `f48db045688df999d0486507bc6c3aeac4213f98`; GitHub comparison confirmed `main` was identical to that merge commit. The corresponding post-merge Linux and Windows workflows were triggered separately. This confirms the fix was integrated after passing branch CI; it does not claim that the user's PC was tested.
