# I-01 Full Plan — Administrative Audit and Independent Emitter Blueprint

**Status:** OPEN / FAIL-CLOSED / NOT CLEARED FOR LIVE SUBMISSION  
**Scope:** preparation and read-only audit only. No emitter repository, GitHub settings, PR, merge, or live write is created or enabled by this document.

## 1. Verified snapshot

- Repository: araragijona-coder/botpyton (public).
- main observed at ee8ca6ae800e6ed14bb67b87dc602f1289e50bc3.
- Simulation candidate: 4c07ebbd85ddcf85866f73f0b7ec0a60215365ec.
- CI run: https://github.com/araragijona-coder/botpyton/actions/runs/38987022365 — conclusion success; head_sha matches the candidate; test log reports 96 tests and OK.
- The run has pull_requests: []; it did not create a PR.
- Repository-level rulesets query returned an empty list. This does not prove organization-level rulesets are absent.
- Branch enumeration showed protected:false. Direct branch protection read for main returned HTTP 403 Resource not accessible by integration. Treat this as UNKNOWN, not proof of no protection.
- Actions permissions endpoints were rejected by the connector allowlist; effective repository and organization settings remain UNKNOWN.
- .github/workflows was not found on main; candidate branch contains workflows. This does not establish whether an organization policy overrides repository settings.
- No secret values or privileged credential contents were read.

## 2. Administrative verification checklist

Capture dated screenshots or exportable settings evidence, with sensitive values redacted.

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
| Candidate CI on exact SHA | VERIFIED | Workflow run head SHA matches; success; 96 tests OK |
| Live sender disabled in candidate workflows | VERIFIED in candidate source | sender job has if:false and read-only permissions |
| Repository rulesets | OBSERVED EMPTY | Does not rule out organization rulesets |
| main branch protection | UNKNOWN | API 403 from integration |
| Effective Actions policy | UNKNOWN | Endpoint inaccessible through connector |
| Credential isolation | UNKNOWN | Access boundary not independently audited |
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

- Stage A — Administrative audit: incomplete until branch/ruleset, Actions policy, actors, environments, and credential isolation have evidence from an authorized settings view.
- Stage B — Emitter blueprint: prepared here; no repository or secret created.
- Stage C — Adversarial simulation tests: pending implementation and CI.
- Stage D — Independent trust-boundary review: pending separate repository, ownership/protection evidence, and review of actual credential path.
- Final I-01 exit: blocked until every required gate is VERIFIED. No live PR submission, no merge, and no sender activation in this phase.
