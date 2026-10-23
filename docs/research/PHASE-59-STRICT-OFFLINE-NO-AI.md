# Phase 59 — Strict offline policy and external AI removal

## Baseline and boundaries

- Parent SHA: 8611ff56f27f4de74d8f3509f29f5a3f35ffc6ae
- Isolated branch: bot-bootstrap/phase-59-offline-no-ai
- Goal: remove the model-provider route and make every legacy adviser command fail closed before it reads any selected input.
- No changes to `main`; no pull request opened or merged.

## Changes

- Replace the former remote adviser implementation with a compatibility shim containing no HTTP client, endpoint, API-key handling, or upload logic.
- Intercept `--architect-ai`, `--allow-external-ai`, `--architect-ai-context`, `--send-source-context`, `--architect-ai-failure-report`, and `--send-failure-report` immediately after argument parsing.
- Return `disabled_offline_policy` with `network_used: false`, no uploads, no commands/code execution, and no file writes. The supplied brief/source/report paths are not opened.
- Keep `--architect-plan` deterministic and offline.
- Replace outdated README and adviser/source-context/repair-feedback instructions with explicit retired-feature notices.
- Add regressions that prove the compatibility shim has no network client/endpoint, direct adviser calls fail without network, every legacy flag is rejected before file access, and local planning remains available without network.

## Network boundary clarification

This phase removes **external AI/model-provider** requests. The bot's explicit GitHub publication capabilities remain a separate opt-in network mode because creating a branch/commit/pull request necessarily communicates with GitHub. This phase does not claim those explicit remote GitHub commands are offline; ordinary local planning, inventory, diagnostics and local patch checking do not need an AI provider.

## Acceptance and limitations

- Both foundation CI and Windows low-memory smoke must pass on the same final commit SHA.
- These tests block Python-level URL opening and socket-connection attempts in the relevant paths; they do not prove that every possible third-party/native module in every unrelated command is globally incapable of network access.
- No `main` changes, PR creation, or merge.
