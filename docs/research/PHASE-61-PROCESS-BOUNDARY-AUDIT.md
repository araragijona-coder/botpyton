# Phase 61 — process and Windows execution-boundary audit

## Baseline and scope

- Parent SHA: ebfc080331aae3d9c13b274d399a63482cdcf3f4
- Isolated branch: bot-bootstrap/phase-61-process-boundary-audit
- Focus: subprocess launch policy, Windows command wrappers, output/time/memory bounds, and honest disclosure of network limits.
- No changes to \`main\`; no PR created or merged.

## Findings and hardening

### Bounded process runner

\`bot/process_runner.py\` requires an absolute executable path matching an explicit allowlist; a working directory inside the selected project root; separate NUL-free argv strings; \`shell=False\`; no inherited stdin; closed file descriptors; bounded output; bounded execution time; filtered secret/code-loading environment variables; and process-tree termination on timeout. On Windows it attempts a Job Object memory cap and descendant cleanup. Memory enforcement reports as active only on Windows.

The runner does **not** use an OS firewall, network namespace, or other network-sandbox mechanism. It now emits these fields in its structured result:

- \`network_isolation_enforced: false\`
- \`child_may_access_network: true\`
- \`windows_batch_wrapper_may_use_cmd: true|false\`

These are capability disclosures, not claims that a particular command did make a network request. If the allowlisted executable is a Windows \`.bat\` or \`.cmd\` file, Windows may involve \`cmd.exe\` even though Python receives \`shell=False\`.

### Project-owned checks

Project-check discovery is read-only and reports that it does not execute commands. An actual test/build check still requires an explicit \`execute_project_code=True\` authorization in the API or \`--execute-project-code\` in the CLI. The result now separately states that the selected project code may access the network and is not network-sandboxed. Human authorization is not equivalent to sandboxing.

### Windows PC-care command execution

The new static regression parses every direct \`subprocess.run\` call in \`bot/pc_care.py\` and fails if any call omits a timeout, omits the \`shell\` setting, or sets \`shell=True\`. This includes local inventory commands and the separately opt-in WinGet update listing. Existing runtime guards still validate the PowerShell/WinGet executable path, apply timeouts, and keep installation/upgrading separate from listing.

A second static regression checks that \`bot/process_runner.py\` uses \`shell=False\` and \`close_fds=True\` for its single \`Popen\` launch site. Runtime tests validate the emitted boundary disclosure on both Linux and Windows CI.

## Limitations

- These changes do not sandbox arbitrary third-party project code, deny networking at the OS level, or prove that all indirect/native extensions are unable to connect to hosts.
- \`shell=False\` is not sufficient to imply that a Windows \`.bat\`/\`.cmd\` program never involves the command interpreter; the result reports that condition instead of hiding it.
- A hosted Windows CI runner is not the user's physical Windows PC.
- Repository administrative evidence for required branch protection remains a separate governance task.

## Acceptance

- All new and existing foundation unit tests pass on the exact final commit.
- All new and existing Windows low-memory smoke tests pass on that same commit SHA.
- No branch is merged, no PR is opened, and \`main\` remains unchanged.
