# Phase 26 — API architecture adviser with explicit source context

**Goal:** improve the optional emergency programming adviser with actual implementation evidence while preserving offline-first operation and no paid-model default.

## Existing API path and cost boundary

The repository already has an opt-in connection through OpenRouter's `openrouter/free` model router. It is not a fixed individual model: the router can choose among free models, so response quality and availability can vary. OpenRouter currently lists $0 token pricing for this router and the Free plan's limit as 50 requests/day and 20 requests/minute; failed attempts count towards the cap. These limits and the router's available models can change. The assistant does not have or install the user's API key: the owner must create it and provide it locally as `BOTPYTON_OPENROUTER_API_KEY`.

We did not add direct OpenAI or Gemini billing endpoints in this phase. A separate paid API route would require provider-specific keys and may incur fees; that should not be enabled implicitly in a project intended to cost $0.

## Finding: the adviser could not review the code being discussed

Before this phase, the remote adviser only received selected brief fields and the deterministic plan, not source files. This protected privacy but substantially limited its ability to reason about actual function signatures, state transitions, tests, and error paths. It could provide general architectural advice, but could not claim to have reviewed the implementation.

## Implementation

- Add repeatable `--architect-ai-context RELATIVE_PATH` to choose exact files inside `project_root` in the structured brief.
- Require both `--allow-external-ai` and a second, explicit `--send-source-context` consent before any selected source text is sent. Without the second consent, return `source_consent_required` and do not call the network.
- Limit one request to four selected files, 16 KB per file, 40 KB total source text, 70 KB serialized request, 30-second timeout, 1,400 generated tokens, and zero automatic retries.
- Restrict paths to project-relative forward-slash paths; reject traversal, unsupported extensions, sensitive/generated folders, symlinks/reparse points, non-regular files, non-UTF-8/binary-like data, and common secret-like patterns.
- Include only selected files alongside the same high-level plan. Keep the project root, full inventory, other files, and any unselected paths excluded. Report the selected relative paths in the result.
- Treat file contents/comments/strings as untrusted evidence. When source files are supplied, the model is additionally asked for a clearly labeled `BORRADOR DE PARCHE (NO APLICADO)` unified diff limited to those paths. The diff remains untrusted text; it is not parsed into actions, applied to source files, executed, or sent into the task queue. If the evidence is insufficient, the model is asked not to fabricate a patch and to state the missing evidence.

The secret pattern scan is intentionally conservative and incomplete: users must inspect the selected files and all free-text fields. It cannot guarantee that every credential format will be detected. The source data leaves the PC only when both explicit consent flags are supplied.

## Reproducible use

Run at BOTPYTON's repository root with a valid brief `project_root`, then choose exact relative paths:

```powershell
python -m bot.main --architect-ai .cerebro/examples/architect-design-brief.json --allow-external-ai --architect-ai-context bot/queue.py --architect-ai-context tests/test_queue.py --send-source-context
```

To use no network, use `--architect-plan` rather than `--architect-ai`. To receive remote advice about the brief without disclosing code, omit both context flags.

## Regression coverage

The mocked tests verify that (a) the source context call is refused without the second consent, (b) only chosen relative file content is in the HTTP payload, (c) absolute project paths are not transmitted as source metadata, (d) path traversal is rejected, (e) secret-like content is rejected before network access, and (f) the CLI enforces the second consent. Tests use no real API key and consume no provider quota.

## Upstream references and design lessons

- OpenRouter documents `openrouter/free` as a zero-token-cost router over available free models: https://openrouter.ai/openrouter/free
- OpenRouter's official pricing page lists the current Free model request caps (20/minute, 50/day) and notes failed free requests consume the daily cap: https://openrouter.ai/pricing
- OpenAI's official API-key safety guidance advises against committing API keys and recommends environment variables: https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety
- BleachBit 6.0.6 (published 2026-10-17) lists startup/scan speed improvements, lazy imports, reduced executable-compression overhead, and fixes for a preference-saving bug and a multi-path wipe failure. It is an example of tying upgrades to specific symptoms and fixes rather than adding a heavyweight dependency without evidence: https://www.bleachbit.org/news/bleachbit-606

Those notes inform engineering criteria; they do not demonstrate a performance improvement for BOTPYTON. This phase improves diagnostic context, not automatic patching or universal programming comprehension.
