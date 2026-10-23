# Phase 69 — read-only GitHub publication reconciliation inspector

## Purpose

The inspector validates an existing declarative change plan before any network request, then uses bounded GET-only checks for the repository/base refs, deterministic branch, diff, planned-file content and matching pull requests. It only reports evidence and recommendations; it never resumes or publishes.

## Command

```powershell
python -m bot.github_publication_inspector --repository araragijona-coder/botpyton --plan .cerebro/examples/github-change-plan.json
```

The existing GitHubClient credential resolution is reused (GITHUB_TOKEN or authenticated GitHub CLI). Do not put a token in the plan or command line. The plan's base branch must match the repository default branch, consistent with the publisher.

## Outcomes

- `safe_to_retry_same_plan`: a complete bounded branch inventory confirmed the deterministic branch absent and no matching PR exists, or the existing branch's content matches the exact plan and its matching PR is compatible with the idempotent publisher. For a branch at the base commit, at least one planned file must be confirmed different from base.
- `manual_review_required`: conflicting branch/content/PR state, a closed PR, a ref moving during inspection, an unverifiable planned file, an unexpected response, or incomplete evidence.
- `blocked`: invalid plan, inaccessible repository/base, authentication/policy block, or plan base not equal to the repository default branch.

A single HTTP 404 never proves an individual ref, file, repository, or PR is absent. Branch absence is accepted only after a complete paginated branch listing corroborates a branch-ref 404 and a second listing rechecks absence after the PR query. Branch listing is capped at 10 pages of 100; PR inspection is capped at five pages of 100. Hitting a cap fails closed.

## Evidence and limits

The JSON report includes canonical plan fingerprint, deterministic branch name, observed ref SHA, bounded changed-path evidence, matching PR number/state/optional URL, fixed reason codes, and trust limits. It omits file contents, PR body/title, raw response bodies, authorization headers, and credentials. Raw API error text is never copied into the report.

The inspector re-reads the candidate ref after the diff/content/PR checks and requires the SHA to remain unchanged. This reduces, but cannot eliminate, races: GitHub does not provide an atomic snapshot across endpoints. A concurrent change after the final GET remains possible, and a publication attempt must independently revalidate state before writing. The compare endpoint may truncate at 300 changed files; reaching that boundary is treated as untrusted. Existing plan validation keeps the change set bounded (20 files and per-file/aggregate byte caps). No new dependency or AI integration is introduced, and the inspector does not issue POST/PATCH/PUT/DELETE.

## Regression coverage

Offline tests cover absent branch with complete inventory, branch still at base, partial and exact content, unexpected files, open/closed PR, HTTP 403/404, transport/service failure, ref movement, invalid-plan validation before network, no-op plans, ambiguous 404 and branch-inventory limits. Tests also assert GET-only calls and credential/error-message non-disclosure. GitHub-hosted CI does not verify the user's local credential or publish real changes.

## Handoff

After CI passes, run Phase 67's read-only preflight on the user's PC and review its JSON. CI is not permission to publish. A real publication requires separate explicit authorization and review of the exact plan, producing only a draft PR. Automatic merge remains disabled.
