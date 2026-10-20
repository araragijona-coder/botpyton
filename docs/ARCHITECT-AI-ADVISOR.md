# External architecture adviser — retired

**Status: disabled as of Phase 59.**

BOTPYTON no longer has an OpenRouter/model-provider integration. No API endpoint, provider key, model request, selected-source upload, or failure-report upload is available through the architecture-adviser compatibility module.

The following legacy CLI flags are retained solely to produce an explicit fail-closed result: `--architect-ai`, `--allow-external-ai`, `--architect-ai-context`, `--send-source-context`, `--architect-ai-failure-report`, and `--send-failure-report`. Any of them returns `disabled_offline_policy` before opening its supplied path or loading a networking client. The module `bot.architect_ai` is now a small compatibility shim that raises `ArchitectAIError(network_used=False)`.

Use the deterministic local planner:

```powershell
python -m bot.main --architect-plan .cerebro/examples/architect-design-brief.json
```

**Network boundary:** local inspection, deterministic architecture planning, local diagnostics, and local patch validation do not need an AI provider. Explicit GitHub operations such as publishing a reviewed branch/commit/PR are separate opt-in operations and intentionally require GitHub network access. This document does not claim that those explicit remote GitHub operations are offline.

Phase 59 regressions verify that the legacy flags do not read their named input files, call `urllib.request.urlopen`, or open a network connection.
