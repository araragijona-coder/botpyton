# I-01 — Administrative Evidence Request and Workflow Audit

**Status:** ACTION REQUIRED FROM REPOSITORY OWNER; no settings were changed by this audit.  
**Observed candidate SHA at audit start:** `bd85c95c761575baf7b4d3daebd9cebd7389c041`.  
**Default branch observed:** `main` at `ee8ca6ae800e6ed14bb67b87dc602f1289e50bc3`.

## 1. Discrepancy requiring reconciliation

The operator previously marked branch protection, Actions permissions, workflow approval controls, and credential inventory as complete. The latest repository API read returned:

- `GET /repos/araragijona-coder/botpyton/branches/main`: `protected: false`, `protection.enabled: false`.
- `GET /repos/araragijona-coder/botpyton/rulesets`: empty list.
- Effective Actions permissions endpoints were not accessible through the connected integration.
- No secrets or credential values were read.

This does not establish that no organization-level or enterprise-level rules apply. It does mean the repository-level branch API does not currently evidence protection. Treat the control as **CONFLICT / NOT VERIFIED** until reconciled with the effective settings.

## 2. Owner checklist — perform in GitHub UI

Do these manually as a repository or organization administrator. Do not attach secret values.

### A. Protect the default branch

1. Open the repository's **Settings → Rules → Rulesets** and **Settings → Branches**.
2. Check whether a branch ruleset targets `main`; record whether it is active, its scope, and bypass actors.
3. If no effective rule protects `main`, create or repair the appropriate rule: require pull requests, require trusted status checks, require independent approval where supported, block force-push and deletion, and restrict bypass to named administrators.
4. Save the configuration, then re-read the branch and ruleset settings. If an organization/enterprise policy is the controlling rule, capture its scope and owner rather than assuming the repository rule is effective.

### B. Verify Actions defaults and workflow control

1. Open **Settings → Actions → General** at repository scope and, where applicable, organization scope.
2. Record the effective default `GITHUB_TOKEN` permission; prefer read-only.
3. Record whether GitHub Actions can create/approve pull requests, which actions are allowed, who may create or approve workflow runs, and the fork/untrusted-workflow approval policy.
4. Verify which roles can edit workflows, secrets, variables, environments, and ruleset bypass lists.
5. Capture screenshots with URL and UTC date/time visible where practical. Redact secret names only if they themselves are sensitive; never capture values.

### C. Verify credential isolation

1. Inventory repository and organization secrets, variables, environments, GitHub App installations, and automation identities that could write to repositories.
2. For each potential write credential, record owner, installation/repository scope, permissions, rotation/revocation owner, and which jobs can access it.
3. Prove the worker identity cannot read or alter the future sender credential, sender workflow, sender environment, or policy.
4. Do not paste tokens, private keys, secret values, or unredacted credential payloads into chat, logs, artifacts, or task JSON.

### D. Supply evidence

Attach or transcribe only redacted evidence and include:

- Control name and effective scope (repository, organization, or enterprise).
- Settings URL.
- Observed state and bypass identities/roles.
- Checker and UTC timestamp.
- Evidence that a later API read agrees with the UI, or an explanation of why the API's `protected:false` is not the effective control.

Do not mark a control verified based on a checkbox or assertion alone.

## 3. Candidate workflow source audit

Audited source files on `bot-bootstrap/phase-09-i01-full-plan-candidate`:

- `.github/workflows/bot-tests.yml`: top-level `contents: read`; test/CI workflow; uses third-party actions by version tag.
- `.github/workflows/botpyton-github-ops.yml`: top-level and validation-job permissions are read-only; checkout follows `github.ref`; `submit-draft-pr` is explicitly `if: false`, has read-only permissions, no checkout, no token binding, and no API submission step.
- `.github/workflows/botpyton-worker.yml`: top-level permissions are `actions: read` and `contents: read`; it accepts declarative task/plan JSON, validates it, and executes worker code from the selected branch. It uses read-only artifact lookup/download and contains `submit-draft-prs` guarded by `if: false`, with read-only permissions and no sender execution steps.

### Interpretation and limitations

- No live sender job is enabled in the inspected candidate workflow sources.
- The worker and validation code is still branch-controlled input. Read-only permissions limit the impact, but do not make that code an independent trust boundary.
- Actions are referenced by mutable version tags (for example `actions/checkout@v4`, `actions/setup-python@v5`, and `actions/github-script@v7`). Pinning third-party actions to reviewed full commit SHAs should be a separate hardening task before any privileged workflow exists.
- This source audit does not establish effective organization/enterprise policy, repository secret accessibility, or the absence of credentials outside these workflow files.
- The sender's `if: false` condition is a containment guard, not a permanent trust boundary: a later branch change could alter it. Do not grant write permissions or activate a sender in this repository.

## 4. Gate status

| Control | Status |
|---|---|
| `main` protection | CONFLICT / NOT VERIFIED |
| Effective Actions token defaults | UNKNOWN — settings endpoint inaccessible through this connection |
| Workflow approval / dispatch permissions | UNKNOWN — administrative evidence required |
| Credential inventory and worker isolation | UNKNOWN — administrative evidence required |
| Candidate sender jobs disabled | SOURCE-VERIFIED at the audited SHA only |
| Independent trusted sender | NOT ESTABLISHED |
| Live write permission or sender activation | NOT AUTHORIZED |

**No GitHub settings, secrets, environments, repositories, PRs, or live writes were created or modified by this audit.**
