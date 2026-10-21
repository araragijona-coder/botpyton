# Phase 60 — Network boundary audit

## Baseline and scope

- Parent SHA: ca7ca605d1b5f3ee8a18e603c60a26633079534e
- Isolated branch: bot-bootstrap/phase-60-network-boundary-audit
- Goal: inventory built-in outbound-network entry points, preserve explicit opt-ins, and add regressions that fail if direct Python HTTP capability spreads into unrelated core modules.
- No changes to \`main\`; no PR created or merged.

## Findings

### 1. GitHub publication gateway — explicit network opt-in

- Implementation: \`bot/github_ops.py\`, via the standard-library \`urllib.request\` client.
- Destination: fixed origin \`https://api.github.com\`; the client rejects absolute/external endpoint strings and accepts repository-scoped \`/repos/...\` API endpoints only.
- Dry-run is the default for \`--github-plan\` and \`--github-plans\`; network writes require the separate \`--execute-github-plan\` or \`--execute-github-plans\` switch.
- Credentials are read from \`GITHUB_TOKEN\` or an already-authenticated GitHub CLI session; tokens are not intentionally printed in reports.
- The CI plan-validation workflow and worker have read-only repository permissions. Both privileged draft-PR sender jobs currently use \`if: false\`, so live workflow submission remains disabled.

### 2. Windows WinGet update listing — separate, explicit network opt-in

- Implementation: \`bot/pc_care.py::check_application_updates\`, invoked only by \`--pc-update-check\` or the GUI's separate update-check action after a confirmation prompt.
- WinGet must resolve to the expected per-user WindowsApps executable alias and the exported source must match the fixed Microsoft HTTPS URL and trusted-source metadata.
- The command is a bounded \`list --upgrade-available\` query using \`shell=False\`; it never runs \`install\` or \`upgrade\`. Existing tests cover non-Windows behavior, the trusted-source requirement, untrusted-source rejection, and timeouts.
- This feature **does access the Internet by user opt-in**. It is not covered by the local offline-only guarantee. It does not require an AI service or paid API.

### 3. Execution of project-owned tests/build scripts — explicit code-execution boundary

- Project check discovery is read-only. Running a check requires \`--execute-project-code\`; a patch-check similarly requires both an exact check ID and that explicit switch.
- BOTPYTON uses fixed, discovered commands with bounded timeouts and process controls; it does not interpret task descriptions as shell commands.
- Once project-owned code is executed, that code may itself open network connections, invoke its own package managers, or download assets. BOTPYTON does not claim to sandbox or globally prevent network access by arbitrary third-party project code. Review the command and code before authorizing it.

### 4. GitHub Actions / CI networking

- CI necessarily communicates with GitHub and downloads pinned Actions/Python. Workflows use full commit-SHA action pins, and checkout actions disable persisted Git credentials.
- Foundation and Windows smoke workflows declare \`contents: read\`. The persistent worker's active job permissions are \`actions: read\` and \`contents: read\`; its live sender remains disabled.
- These are CI runner network activities, not background activity of the local desktop application. CI is evidence from hosted runners, not a physical-PC test.

## Regression coverage added in this phase

- AST-based scan: direct imports of common external HTTP/model clients are forbidden in \`bot/\`; direct \`urllib.request\` imports must remain confined to \`bot/github_ops.py\`.
- GitHub plan dry-run must not invoke \`urllib.request.urlopen\`.
- Arbitrary absolute HTTP endpoints must be rejected before any URL opener is called.
- The standard local PC-care audit must not invoke the optional WinGet update checker.
- Project check execution still requires the explicit authorization flag.
- Both CI draft-PR sender jobs must remain disabled and must not gain write permissions.

## Limits of the audit

This is an auditable allowlist of the direct networking paths found in the inspected built-in Python modules and workflows, not a mathematical proof that every native dependency, OS facility, user-selected command, or third-party project is incapable of network access. The two intentionally networked built-in actions are GitHub publication and the explicitly invoked WinGet update listing; authorized project-owned code is a separate trust boundary. Ordinary built-in local planning, inventory, diagnostics and deterministic operations do not need those external services.

Branch protection/ruleset administration remains a separate outstanding governance check because the available connection did not provide independent administrative evidence. No physical Windows PC was tested in this phase.
