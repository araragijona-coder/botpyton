# I-01 — PR emitter trust boundary

Status: **FAIL-CLOSED / NOT CLEARED FOR LIVE SUBMISSION**

## Current containment

The workflows on this development branch keep both PR submission jobs disabled with `if: false`. Their job-level permissions are read-only, they do not check out repository code, they do not bind `secrets.GITHUB_TOKEN`, and they do not call the GitHub plan executor. Static regression tests enforce these properties.

This is containment, not proof that a privileged sender can safely be enabled. A workflow file on a branch is itself branch-controlled input. A `if: false` guard can be edited by a later commit, so it is not an independent trust boundary.

## Required architecture before any live sender is considered

1. Keep the sender disabled in this branch and in all development/untrusted workflow definitions.
2. Put any future sender only in a workflow revision reviewed and installed on the repository default branch. The sender must not check out, import, or execute code, scripts, actions, or configuration from the triggering branch or task plan.
3. Treat the branch payload as untrusted data. Validate it in a separate read-only job, then pass only a bounded, schema-validated artifact or scalar outputs to the trusted sender. The sender must independently validate the payload again.
4. Scope the sender job to the minimum required permissions (`contents: write`, `pull-requests: write`) and bind its token only in the single step that performs the required API operations. Never grant write permissions to validation or worker jobs.
5. Prefer a GitHub App or narrowly scoped credential that is unavailable to branch-controlled workflows. Do not use a broad personal access token. Require draft PRs; never merge or write directly to the default branch.
6. Configure default-branch protection/rulesets outside this branch: require review and passing checks, restrict who can push, and prevent unreviewed changes to workflows/security policy. Also configure Actions token permissions to read-only by default and restrict who can approve or dispatch privileged workflows.
7. Test the boundary without performing live writes: hostile-branch workflow mutations, task JSON containing executable-looking strings, unexpected artifact fields, missing/expired artifacts, and invalid/non-draft plans must all fail closed.

## Administrative evidence still missing

The connected GitHub integration cannot read the branch-protection endpoint (HTTP 403). The repository reports no rulesets, and its main branch is reported as unprotected. The current default branch also has no `.github/workflows` directory, so a trusted default-branch sender cannot be established by changing only this development branch.

These controls require repository-owner action or a separately authorized administrative connection. Do not infer that protection exists from passing unit tests.

## Exit criteria

I-01 may be marked fully verified only when all of the following are evidenced:

- A trusted sender definition exists on the default branch and its exact SHA is reviewed.
- The sender has no code-execution or checkout path from an untrusted branch.
- Repository rules/protection and Actions token defaults are independently verified.
- Static hostile-input/security regression tests pass.
- CI is green on the exact final commit SHA.
- A dry-run or mocked end-to-end boundary test demonstrates that rejected inputs cause zero write requests.

Until then: live PR submission stays disabled; no merge and no promotion to `main`.
