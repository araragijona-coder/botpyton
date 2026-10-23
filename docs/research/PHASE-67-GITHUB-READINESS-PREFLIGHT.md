# Phase 67 — GitHub real-credential readiness preflight

## Goal

Add a manual, read-only integration probe so BOTPYTON can report which of its GitHub read paths are actually available with the credential selected on the user's PC, before any write workflow is attempted.

## Use

```powershell
python -m bot.main --github-preflight araragijona-coder/botpyton
python -m bot.main --github-preflight araragijona-coder/botpyton --github-branch main --github-intent draft-pr
```

The command uses BOTPYTON's existing GitHub credential selection (GITHUB_TOKEN or an authenticated GitHub CLI installation), never prints credentials, and performs GET requests only. The report contains repository metadata, selected branch reachability, a bounded pull-request read, and a bounded Actions-run read. HTTP 403 and 404 are deliberately described as permission/policy-blocked or missing/not-accessible: the client cannot safely distinguish every private-resource and organization-policy case.

## Write readiness is intentionally not guessed

For the draft-pr intent, the report lists the fine-grained token permissions documented for its future content and pull-request operations. It explicitly returns `not_verified_by_read_only_probe`; it does not attempt a test push, create a branch, commit, pull request, or mutate repository settings. Successful reads do not prove write scopes, branch policy acceptance, SSO authorization, workflow policy, or ability to create a draft pull request.

## Industry references

- GitHub's official fine-grained-token permissions matrix documents endpoint-specific requirements and the X-Accepted-GitHub-Permissions response header: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
- The GitHub CLI official authentication-status manual describes how gh auth status checks active accounts and authentication state: https://cli.github.com/manual/gh_auth_status

## Security and verification

- Only repository-scoped GET requests are issued.
- No credentials, authorization headers, raw response body, repository source code or secret material are included in the output.
- Authentication, permission, inaccessible-resource, rate-limit, API and transport failures produce bounded machine-readable statuses.
- Regression tests use an in-memory fake transport and assert that the method for every probe is GET; tests do not need a real token or network access.
- No GitHub write, merge, main-branch update, pull request or new dependency is required.

## Limits

This verifies actual read endpoints with the credential available on the machine where BOTPYTON runs. It is not proof of write authorization and does not replace a real, user-approved end-to-end draft-PR operation. CI runners do not exercise the user's locally stored GitHub credential.