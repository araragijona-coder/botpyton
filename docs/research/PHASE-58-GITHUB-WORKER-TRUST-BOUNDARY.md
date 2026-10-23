# Phase 58 — GitHub worker trust boundary

## Baseline and scope

- Parent SHA: eae3e85d2f7e42de44844e2dcd714d46fdd45fa0
- Isolated branch: bot-bootstrap/phase-58-worker-trust-boundary
- Purpose: reduce accidental execution from the default branch and make each worker job's token permissions explicit.
- No changes to `main`; no pull request opened or merged.

## Changes

- Gate the persistent worker's input-validation and execution jobs so a manual dispatch on `refs/heads/main` cannot run either job.
- Declare the execution job's permissions at job level as `actions: read` and `contents: read`; the validator retains only `contents: read`.
- Gate GitHub plan validation from `main` as well. It remains a read-only validator and does not create branches, commits, or pull requests.
- Add static tests that assert the job-level gates and exact worker permissions so a later edit cannot silently remove them.
- Keep both live draft-PR sender jobs explicitly disabled. This phase does not enable them or add write tokens.

## Existing security properties rechecked

- Workflow triggers remain `workflow_dispatch` and limited legacy branch pushes for the worker; neither privileged workflow uses `pull_request_target` or `workflow_run`.
- All external actions remain pinned to full commit SHAs, and checkouts use `persist-credentials: false`.
- Top-level and job-level token permissions remain read-only.

## External controls and limitations

- The GitHub API exposed no repository rulesets in this audit. The connected integration could not read branch-protection settings (HTTP 403), so required reviews/checks and administrator-enforced protection are **not independently verified by this phase**. A repository administrator must verify/configure them in GitHub settings.
- Read-only CI and job gates reduce exposure; they are not a full OS sandbox and do not prove safety against an administrator, compromised runner, or a future workflow change that grants more permissions.
- Workflow conditions are tested statically. We do not dispatch the worker on `main` or test privileged actions, because no privileged action is enabled.

## Acceptance

- Foundation tests and Windows low-memory smoke must pass against the same final SHA.
- No `main` changes, PR creation, or merge.
