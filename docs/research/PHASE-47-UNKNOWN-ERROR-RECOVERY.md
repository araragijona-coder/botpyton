# Phase 47 — Deterministic investigation of unknown failures

## Baseline and scope

- Repository: araragijona-coder/botpyton
- Parent candidate: d54adbd0ba1d3a4809f069a38dc66ccba040fcf0 (Phase 46, CI green)
- Module: bot/unknown_recovery.py
- CLI: --investigate-failure with optional command/exit-code/expected/observed context
- main remains unchanged; no PR, merge, project execution, network, or automatic source edit is part of this phase.

## Defect addressed

The existing recovery catalogue could classify known signatures and advise a human when a signature was unknown. It did not provide a single deterministic investigation report that combined bounded static checks, explicit falsifiable hypotheses, missing evidence, and a clear next step. The new planner provides that structure without pretending that an unknown error can safely be fixed by guessing.

## Method

1. Validate an existing project root and reject symbolic/reparse-point path components.
2. Accept a UTF-8 log up to 64 KiB and record its SHA-256. Sanitize displayed log/context snippets for common credential patterns.
3. Scan deterministically under hard limits: at most 5,000 directory entries, 500 candidate files, 200 KiB per file, and 2 MiB total bytes read. Skip generated/build/virtual-environment directories and symbolic/reparse entries.
   Before static parsing, revalidate candidate identity and regular-file status. A file that disappears, becomes inaccessible, or changes into a link/reparse entry is skipped and counted instead of aborting the full investigation. A regression simulates removal after inventory.
4. Run only static parsers: Python AST parse without import/execution; JSON decode; TOML parse for pyproject.toml. Detect explicit test-contract, timeout/resource, permission/lock, missing-module and external-connection signals from log text.
5. Build a ranked hypothesis only when a concrete signal supports it. Each hypothesis includes evidence identifiers, one discriminating experiment, a condition that would refute it, and root_cause_confirmed=false.
6. If no hypothesis has sufficient evidence, return needs_more_evidence and list required context. Invalid attempt budgets return blocked_attempt_limit. Automatic retry and source edit remain disabled.

## Regression coverage

The tests use temporary folders and cover genuinely unknown errors, static parser failures, deterministic output, redaction, known-signature interaction, log/attempt limits, symlink-root rejection where supported, skipped generated trees, bounded scan truncation, CLI argument contracts, and mutually exclusive diagnostics.

## Safety statement

The investigator does not run project tests or compilers. It does not infer permission to run a test from a manifest, call the network, install a dependency, modify the worktree, or retry. To test behavior, operators must choose a recognized check and use the existing explicit --execute-project-code permission. A static syntax/JSON/TOML finding is evidence about the scanned revision; correlation with the original runtime failure remains unconfirmed.

## Upstream principles

- Python AST parsing documents a syntax-tree check that does not execute a program: https://docs.python.org/3/library/ast.html
- Python's JSON and TOML parsers provide deterministic syntax validation interfaces: https://docs.python.org/3/library/json.html and https://docs.python.org/3/library/tomllib.html
- GitHub's secure-use guidance recommends least privilege and treating external content as untrusted data: https://docs.github.com/en/actions/reference/security/secure-use

These sources describe general techniques; they do not establish that any candidate hypothesis is the root cause of a particular user failure.

## Acceptance gate

The Linux foundation tests and Windows low-memory smoke must both pass on this candidate SHA. No claim is made about the user's local Windows install. Any later repair must use a separately reviewed minimal diff, a reproduction/regression test, and the existing isolated patch workflow.
