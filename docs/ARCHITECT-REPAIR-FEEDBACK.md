# Repair failure feedback — remote upload retired

**Status: disabled as of Phase 59.**

Earlier versions of this document described sending a sanitized reviewed-patch failure report, selected source files, and a structured brief to an external model adviser. That upload path has been removed. The related CLI flags remain only for compatibility and always return `disabled_offline_policy` before opening the named files. No brief, source file, traceback, or failure report is transmitted to a model provider.

The local reviewed-patch workflow is unchanged: it validates a human-supplied diff in an isolated worktree, can execute only a selected project check after explicit authorization, and emits local diagnostics. It does not ask a remote model for another patch or upload its logs.

For deterministic local planning, use:

```powershell
python -m bot.main --architect-plan .cerebro/examples/architect-design-brief.json
```

For the authoritative boundary and regression details, see [Phase 59 — strict offline/no-AI policy](research/PHASE-59-STRICT-OFFLINE-NO-AI.md).
