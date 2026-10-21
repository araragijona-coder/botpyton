# Phase 68 — GitHub publication failure-injection and retry recovery

## Goal

Verify the existing declarative GitHub publication flow at every remote write boundary. A network error does not always tell the caller whether GitHub accepted the request. The same plan therefore needs to be safe to inspect and execute again without duplicating its branch, commit content, or draft pull request.

## Coverage added

`tests/test_github_publication_recovery.py` reuses the in-memory GitHub REST fixture and injects one bounded failure at each write operation:

1. `POST /git/refs` — create the deterministic branch.
2. `POST /git/trees` — create the planned tree.
3. `POST /git/commits` — create the single planned commit.
4. `PATCH /git/ref/heads/{branch}` — advance the branch without force.
5. `POST /pulls` — open a draft pull request.

For each boundary, the test covers both (a) failure before the fake server accepts the request and (b) the server applying the write before the response is lost. It reruns the exact same declarative plan and verifies that the flow converges to one open draft PR with the planned file content and no force update. A separate assertion checks that error/call evidence does not include the synthetic credential.

## Recovery behavior and operator guidance

- If a run stops with a transport/API error, inspect the repository and branch state before making a different plan.
- When the original plan is still the intended change, rerunning the exact same plan is the tested recovery path. The generated branch name is fingerprinted from the canonical plan; existing branches are checked for matching content, closed prior PRs are not duplicated, and an existing matching open PR is reused.
- Do not change the plan contents or task ID while recovering an interrupted attempt; that creates a different fingerprint and branch.
- Treat a conflicting branch, an unexpected extra changed file, a closed prior PR, or an unknown API outcome that fails the tests as a stop-and-review condition. Do not force-push or merge automatically.

## Safety boundary

The new tests are offline and use only an in-memory fake transport. They do not exercise the user's stored token, write to a real GitHub repository, publish any PR, change `main`, or prove the user's credential has write access. Phase 67's local read-only preflight remains the real-credential check. Actual end-to-end publication still requires a separately approved operation after local preflight and plan review.

## Verification

The test suite must be run by the repository's existing Linux and Windows CI workflows on the exact phase commit. CI results are evidence about deterministic code behavior in those runners, not a test of the user's physical PC or locally configured GitHub credential.
