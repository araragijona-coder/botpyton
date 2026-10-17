# I-01 — Independent Emitter Repository Starter Package

**Purpose:** copy-ready preparation package for a future, separate emitter repository.  
**State:** PREPARED ONLY — this file does not create a repository, GitHub setting, secret, environment, PR, or live write.  
**Proposed name:** `botpyton-pr-emitter` (availability and ownership must be checked manually before any creation).

## 1. Trust contract

The worker repository `araragijona-coder/botpyton` is an untrusted request producer. It must not own, read, or modify the emitter's policy, workflow, deployment environment, or credential. The emitter must not checkout, import, execute, or source any branch-controlled worker code. It treats every request field as hostile data.

The simulation in the worker repository is a test fixture only, not an independent security boundary. A future emitter must independently authenticate the caller and resolve the head ref to a commit SHA through an approved GitHub API read. Caller-supplied actor names and SHA strings are not identity or revision proof.

## 2. Starter tree to create only after explicit authorization

```text
botpyton-pr-emitter/
  README.md
  SECURITY.md
  CODEOWNERS
  .github/
    CODEOWNERS
    workflows/
      ci.yml
  policy/
    repositories.json
    actors.json
    policy.schema.json
  emitter/
    __init__.py
    request_schema.py
    identity.py
    github_resolver.py
    policy.py
    idempotency.py
    simulation.py
    audit.py
  tests/
    test_identity.py
    test_sha_resolution.py
    test_policy_denies.py
    test_replay.py
    test_workflow_isolation.py
    test_no_live_transport.py
  docs/
    TRUST_BOUNDARY.md
    OPERATIONS.md
    INCIDENT_RESPONSE.md
```

Initial implementation must remain simulation-only. Do not include a live submission client, deployment workflow, or write credential in the first creation batch.

## 3. Initial policy files

### `policy/repositories.json`

```json
{
  "schema_version": 1,
  "repositories": [
    {
      "full_name": "OWNER/WORKER-REPOSITORY",
      "allowed_base_refs": ["main"],
      "allowed_head_prefixes": ["bot-bootstrap/"],
      "draft_only": true,
      "allow_merge": false,
      "allow_contents_write": false
    }
  ]
}
```

Replace the placeholder only after an authorized operator confirms the exact repository. Reject unknown repositories and unknown keys. Do not accept policy from the worker request.

### `policy/actors.json`

```json
{
  "schema_version": 1,
  "actors": [
    {
      "kind": "github_app_installation",
      "installation_id": "SET_ONLY_AFTER_ADMIN_VERIFICATION",
      "repository_allowlist": ["OWNER/WORKER-REPOSITORY"]
    }
  ]
}
```

The placeholder is intentionally non-operational. Resolve identity from verified GitHub authentication/installation metadata, never from a request JSON field. Keep a human identity allowlist separate from installation identity if both are required.

### `policy/policy.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Emitter repository policy",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "repositories"],
  "properties": {
    "schema_version": {"const": 1},
    "repositories": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "full_name", "allowed_base_refs", "allowed_head_prefixes",
          "draft_only", "allow_merge", "allow_contents_write"
        ],
        "properties": {
          "full_name": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"},
          "allowed_base_refs": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "minLength": 1, "maxLength": 200}},
          "allowed_head_prefixes": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}/$"}},
          "draft_only": {"const": true},
          "allow_merge": {"const": false},
          "allow_contents_write": {"const": false}
        }
      }
    }
  }
}
```

Schema validation is not enough by itself: runtime policy must validate Git ref semantics, authorization, exact repository identity, independently resolved SHA, body/title bounds, idempotency, and replay.

## 4. Initial `README.md` contract

The repository README must state:

- This is an isolated policy-enforcement service, not a general-purpose GitHub automation bot.
- Phase 0 accepts requests and records decisions in simulation mode only.
- No token or installation credential is available to worker jobs.
- No request field can choose an arbitrary API URL, action, workflow, repository, base ref, or permission.
- Any later live submission is a separate, explicitly approved phase requiring review of exact SHA, CI, settings, permissions, and credential path.
- A green unit test suite does not prove administrative settings or organizational trust boundaries.

## 5. Initial workflow policy

The future `.github/workflows/ci.yml` must begin with explicit read-only permissions, a pinned Python version, syntax checks, unit tests, schema validation, and static security tests. It must not contain a live submission job, use `pull_request_target`, execute scripts from a triggering branch with elevated permissions, or reference secrets. Pin third-party actions to full commit SHAs before the repository is used for security-sensitive operations.

A workflow file stored inside this preparation document is not a configured or trusted workflow. Actual workflow installation requires review on the future repository's default branch and verification of its effective Actions settings.

## 6. Protection and credential gates

Before any live write is even considered:

1. Verify repository ownership and make the repository private unless a documented reason requires otherwise.
2. Protect the default branch: required PR, independent review, required trusted CI, no force-push or deletion, and tightly limited bypass.
3. Add CODEOWNERS for workflows, policy, and deployment/credential configuration.
4. Verify Actions default token permission is read-only and restrict who can dispatch or approve privileged workflows.
5. Verify workflow approval policies for untrusted branches/forks and who can modify them.
6. Use a dedicated GitHub App with the minimum repository access. Start read-only; request only `pull_requests: write` if later justified. Do not grant `contents: write` for a draft-PR-only sender.
7. Keep credentials in the isolated sender environment only; never expose them to the worker, artifacts, logs, outputs, or task payload.
8. Record dated, redacted evidence for rules, bypass actors, Actions settings, app installation, secret ownership, and worker non-access.
9. Add a human approval gate for the first live operation and a tested revocation/rollback procedure.

## 7. Mandatory tests before any promotion

- Caller authentication is verified independently from request JSON.
- Head ref resolves to the exact requested immutable commit SHA; moved refs, deleted refs, and ambiguous resolution deny.
- Unknown repository/base/head/actor, malformed schema, unexpected fields, oversized text, non-draft intent, and missing evidence all deny.
- Replays are idempotently rejected, including same request ID with changed payload.
- Denials cause zero API mutation calls.
- Worker-controlled code and workflow files are never checked out or executed by the sender.
- No merge, direct default-branch write, branch deletion, arbitrary workflow dispatch, or arbitrary API URL is possible.
- CI passes on the exact final SHA, and a mocked end-to-end test proves the denial path causes zero write calls.

## 8. Creation handoff

This package is ready for review and later copying. It is not authorization to create `botpyton-pr-emitter`. Before creation, an operator must explicitly approve the repository name, owner, visibility, administrators, and initial maintainers. Before any credential or write permission is added, I-01 must be separately re-authorized after all trust and administrative gates are evidenced.

## 9. Current gate state

- Administrative settings: **CONFLICT / UNKNOWN** — the latest GitHub branch API reports `main` as unprotected, conflicting with the operator checklist. Reconcile in repository settings and attach dated evidence.
- Actions defaults and workflow approval: **OPERATOR-REPORTED / NOT INDEPENDENTLY VERIFIED**.
- Credential isolation: **OPERATOR-REPORTED / NOT INDEPENDENTLY VERIFIED**.
- Adversarial simulation: **in progress**; require green CI on the exact final candidate SHA.
- Independent emitter trust boundary: **NOT ESTABLISHED**; no separate repository or credential has been created.
- Live submission: **DISABLED**.
