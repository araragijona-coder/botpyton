# I-01 Full Plan — Administrative Audit and Independent Emitter Blueprint

**Status:** OPEN / FAIL-CLOSED / NOT CLEARED FOR LIVE SUBMISSION  
**Scope:** preparation and read-only audit only. No emitter repository, GitHub settings, PR, merge, or live write is created or enabled by this document.

## 1. Verified snapshot

- Repository: araragijona-coder/botpyton (public).
- main observed at ee8ca6ae800e6ed14bb67b87dc602f1289e50bc3.
- Current work branch: bot-bootstrap/phase-09-i01-full-plan-candidate.
- Latest adversarial test code SHA: 567b65cf5b52d375762be5a295a48d3d335ecf25.
- CI run: https://github.com/araragijona-coder/botpyton/actions/runs/38987675339 — conclusion success; head_sha matches that exact test commit; test log confirms the full suite passed after the new I-01 tests. The final documentation commit is being validated separately before reporting a final SHA.
- The run has pull_requests: []; it did not create a PR.
- Repository-level rulesets query returned an empty list. This does not prove organization-level rulesets are absent.
- Fresh branch API read for main returned protected:false and protection.enabled:false. This is direct evidence that GitHub's branch-protection API does not report main as protected at the time of the read. Repository-level rulesets query was empty; organization-level or other external controls remain unknown and would need separate evidence. This conflicts with the operator-reported completed checkbox and must be reconciled before I-01 can close.
- Actions permissions endpoints were rejected by the connector allowlist; effective repository and organization settings remain UNKNOWN.
- .github/workflows was not found on main; candidate branch contains workflows. This does not establish whether an organization policy overrides repository settings.
- No secret values or privileged credential contents were read.

## 2. Administrative verification checklist

**Operator-reported checklist (2026-10-16):** the operator marked all four controls below complete: main protection, Actions permissions, workflow approval/restrictions, and credential inventory. This is recorded as an attestation, not as independently verified configuration. No screenshots, settings export, or authorized API evidence was attached in this task. Keep the effective state UNKNOWN until such evidence is recorded. Capture dated screenshots or exportable settings evidence, with sensitive values redacted.

1. Branch rules for main: require pull requests and appropriate approvals; dismiss stale approvals or require approval of latest push where supported; block force-push and deletion; restrict bypass actors; require status checks from trusted workflows.
2. Ruleset scope: verify whether organization-level rulesets apply, whether they target main, and who can bypass them.
3. Actions general settings: record allowed-actions policy; default GITHUB_TOKEN permissions; whether workflows can approve PRs; who can create or approve workflow runs; fork/workflow approval policy.
4. Environment controls: if the future sender uses an environment, require protected branches/tags, required reviewers, and no self-approval where supported. Do not create the environment in this phase.
5. Credential inventory: verify identities, app installations, secrets, variables, and environments that could grant write access. Never print secret values. Record owner, permission scope, rotation/revocation procedure, and proof the worker cannot read the sender credential.
6. Actor and installation inventory: record collaborators, teams, GitHub App installations, Actions bot identities, and bypass actors. Distinguish repository permissions from organization-level permissions.
7. Evidence quality: record checker, UTC timestamp, settings URL, observed value, and redacted evidence. If the integration cannot read a setting, keep it UNKNOWN until an authorized human verifies it.

| Control | Status | Reason |
|---|---|---|
| main ref unchanged by this work | VERIFIED at snapshot | Ref read returned expected SHA |
| Adversarial test candidate CI | VERIFIED | SHA 567b65cf5b52d375762be5a295a48d3d335ecf25; run 38987675339 success on exact SHA |
| Final candidate CI | PENDING | Must verify after final documentation commit |
| Live sender disabled in candidate workflows | VERIFIED in candidate source | sender job has if:false and read-only permissions |
| Repository rulesets | OBSERVED EMPTY | Does not rule out organization rulesets |
| main branch protection | CONFLICT / REQUIRES RECONCILIATION | User checklist marked complete, but fresh branch API returned protected:false and protection.enabled:false; repository-level rulesets query empty; external/org controls not verified |
| Effective Actions policy | OPERATOR-REPORTED COMPLETE / UNKNOWN VERIFIED STATE | User checklist marked complete; endpoint inaccessible through connector; no supporting settings evidence attached |
| Workflow approval and untrusted-workflow policy | OPERATOR-REPORTED COMPLETE / UNKNOWN VERIFIED STATE | User checklist marked complete; effective policy not independently inspected |
| Credential inventory / worker isolation | OPERATOR-REPORTED COMPLETE / UNKNOWN VERIFIED STATE | User checklist marked complete; no secret values read and no independent proof of isolation attached |
| Independent trust boundary | NOT VERIFIED | Simulation module is in worker repository |

## 3. Independent emitter repository — blueprint only

Suggested future name: botpyton-pr-emitter. This is a proposal, not a created resource; verify name availability and ownership manually before any creation.

### Trust boundary

- Worker repository botpyton is an untrusted producer. It may submit a declarative request, but must never receive the sender write credential.
- Emitter repository/service is a separately owned and protected policy enforcement point. Its code, policy, workflow, environment, and credential must not be mutable by the worker identity.
- Use a dedicated GitHub App installed only on explicitly approved repositories. Begin with read-only validation; add pull-request write permission only after all gates pass. Do not grant contents write without a separately approved requirement.
- Store credentials only in the isolated sender environment or secret store. Never pass them to worker jobs, artifacts, logs, task JSON, or output variables.
- Request contract: repository full name, request ID, authenticated actor, base ref, head ref, full commit SHA, title/body, schema version, and explicit draft-only intent. Sender independently authenticates the caller and resolves ref/SHA against GitHub; caller-supplied actor/SHA assertions are not trusted.
- Policy: allowlist repository, base ref, source branch prefix, caller identity, draft-only behavior, title/body limits, request size, replay/idempotency protection, and deny-by-default behavior.
- Execution: one bounded operation per request; no shell or arbitrary workflow execution, arbitrary API URL, workflow-file changes, privilege escalation, merge, branch deletion, or non-draft PR.
- Audit: append-only structured event with request ID, authenticated actor, resolved SHA, policy decision, operation ID/result, and timestamps; redact tokens and sensitive content.

### Proposed repository layout

    botpyton-pr-emitter/
      README.md
      SECURITY.md
      CODEOWNERS
      .github/CODEOWNERS
      .github/workflows/ci.yml
      policy/repositories.json
      policy/actors.json
      policy/policy.schema.json
      emitter/request_schema.py
      emitter/identity.py
      emitter/github_resolver.py
      emitter/policy.py
      emitter/idempotency.py
      emitter/simulation.py
      emitter/audit.py
      tests/test_identity.py
      tests/test_sha_resolution.py
      tests/test_policy_denies.py
      tests/test_replay.py
      tests/test_workflow_isolation.py
      tests/test_no_live_transport.py
      docs/TRUST_BOUNDARY.md
      docs/OPERATIONS.md
      docs/INCIDENT_RESPONSE.md

No live transport or deployment workflow should be present in the initial simulation phase.

### Required protections before any live write

- Dedicated repository, private unless a documented reason requires otherwise.
- Protected default branch, required independent review, trusted required CI, no force-push/deletion, and tightly limited bypass.
- CODEOWNERS for workflows, policy, and credential/deployment configuration.
- Actions pinned to full commit SHAs where practical; least-privilege permissions at workflow and job levels.
- Protected environment and explicit human approval gate for any first live write.
- Worker identity has no write permission to the emitter repository, workflows, environment, or secrets.
- Security tests prove caller identity and commit SHA are independently resolved, replay is denied/idempotent, and malformed or unauthorized requests cause zero API mutations.
- Document and test rollback/revocation.
- Every code change needs exact commit SHA and successful CI on that exact SHA.

## 4. Adversarial tests to strengthen in the simulation candidate

- Non-string and unhashable actor identities.
- Missing, empty, whitespace, and malformed request IDs.
- Policy construction with malformed or empty allowlists and invalid limits.
- Branch refs with control characters, traversal-like components, .lock suffixes, forbidden Git ref sequences, and excessive lengths.
- Exact SHA/ref mismatch when the ref resolves to a different commit.
- Duplicate request IDs with different payloads must reject, not silently reuse.
- Recorder operation immutability and zero mutation for every denial.
- Verify module imports and operates without network/socket/process entry points available, without claiming same-repository code is an independent trust boundary.

Run the full existing test workflow on the final commit. Report exact head SHA and successful CI whose head_sha equals it.

## 5. Stage gates

- Stage A — Administrative audit: operator reports the four checklist controls complete; still incomplete for I-01 exit until the effective branch/ruleset, Actions policy, workflow approval policy, actor inventory, environments, and credential isolation have dated evidence from an authorized settings view.
- Stage B — Emitter blueprint: prepared; copy-ready package is documented in docs/security/I-01-EMITTER-REPO-STARTER-PACKAGE.md. No repository, secret, environment, or other external resource created.
- Stage C — Adversarial simulation tests: expanded and passing on SHA 567b65cf5b52d375762be5a295a48d3d335ecf25; includes invalid identity types, malformed IDs/refs, invalid policy allowlists and limits, duplicate replay with changed payload, returned-record mutation isolation, and static checks against network/process/filesystem imports. Final candidate CI remains required.
- Stage D — Independent trust-boundary review: NOT VERIFIED until the separate repository is explicitly authorized and created later, its owner/protection/credential path are evidenced, and the sender's trust assumptions are reviewed. No creation or activation is in scope now.
- Final I-01 exit: blocked until every required gate is VERIFIED. No live PR submission, no merge, and no sender activation in this phase.

## 6. Latest administrative evidence request and workflow audit

The owner-facing remediation checklist and source-level workflow audit are maintained in [I-01-ADMIN-EVIDENCE-REQUEST-AND-WORKFLOW-AUDIT.md](I-01-ADMIN-EVIDENCE-REQUEST-AND-WORKFLOW-AUDIT.md). It records the exact branch-protection discrepancy, the administrative settings that must be inspected manually, and the scope/limits of the candidate workflow source audit. This does not modify GitHub settings or establish organization-level policy.

Source audit confirms both sender jobs are disabled and have no sender execution steps at the audited candidate revision. The worker workflow still executes branch-controlled worker code with read-only token permissions, and third-party Actions are referenced by version tags rather than reviewed full SHAs. This is acceptable only as current containment; it is not a privileged trust boundary. Action pinning and effective administrative settings must be resolved before any future sender phase.
