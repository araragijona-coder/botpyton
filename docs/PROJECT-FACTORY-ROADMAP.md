# BotPyton Project Factory — Phase 10

## Status

**Implemented as a first deterministic slice; not a general-purpose autonomous programmer.** The factory is a fixed catalogue of reviewed templates. It does not use a language model, AI API, inference service, or AI credentials.

## Run

List templates:

    python -m bot.project_factory --list

Create a new project in a destination that does not already exist:

    python -m bot.project_factory --template card-rpg-web --name "Mi RPG" --destination ./salidas/mi-rpg
    python -m bot.project_factory --template telegram-bot --name "MiBot" --destination ./salidas/mi-bot
    python -m bot.project_factory --template desktop-utility --name "MiHerramienta" --destination ./salidas/mi-herramienta

Templates:
- `card-rpg-web`: playable local browser prototype with turn-based elemental card combat.
- `telegram-bot`: Python polling bot starter; token is read from an environment variable.
- `desktop-utility`: minimal cross-platform PySide6 desktop UI starter.

Each generated project includes `botpyton-project.json`, with template identity and `ai_enabled: false`.

## Safety and determinism

- The template ID must match the built-in catalogue; arbitrary template paths are not accepted.
- Project names are sanitized to a restricted character set.
- The destination must not exist. Existing folders are never overwritten.
- Template paths are checked for absolute paths and parent traversal.
- User text is used only as a project title; it is never executed as code or shell input.
- The generator makes no network calls and installs no dependencies.
- Generated code is a starter and must be tested in its target environment before use.

## What is not implemented yet

This phase does not claim to build any arbitrary application from a natural-language request. The RPG is a playable prototype, not a commercial game. The Telegram bot has a minimal command handler, not a complete bot with database, moderation, admin tools, or deployment. The desktop template is a UI shell, not a full OBS replacement, media player, or mobile application.

## Roadmap toward a capable no-AI engineering worker

1. **Project specification schema:** structured requirements, target OS, language/runtime, offline/network needs, acceptance tests, and prohibited actions. Reject underspecified specs instead of guessing.
2. **Template/plugin contracts:** versioned templates with manifests, declared dependencies, capabilities, setup checks, license/provenance and test commands.
3. **Repository understanding:** language/framework detection, project instruction discovery, dependency inventory, build/test command catalogue, static analysis and file ownership map.
4. **Deterministic planning:** dependency DAG, bounded task queue, exact file changes, preconditions, expected outputs, and human-readable plan before mutation.
5. **Safe local execution:** isolated workspace, explicit command allowlist, time/output/resource limits, no shell interpolation, redacted environment, rollback snapshots and audit logs.
6. **Repair rules:** only well-defined error signatures with uniquely matched fixes; verify the changed file, rerun the smallest test, then the full suite. Unknown errors become a report, not a speculative edit.
7. **Quality gates:** unit/integration tests, formatting, static analysis, dependency/license checks, packaging smoke tests, screenshots where relevant, and acceptance criteria.
8. **Platform builders:** expand separately for Telegram, browser RPG/card games, desktop utilities/media, and mobile apps. Each template family needs real runtime tests and build artifacts before it is marked supported.
9. **GitHub workflow:** work on isolated branches, create draft PRs for review, attach test reports and diffs. No automatic merge or production release.
10. **Human approval boundaries:** credentials, network access, destructive migrations, releases, publishing, signing and deployment remain gated.

## Capability labels

- **IMPLEMENTED:** fixed template scaffolding and basic tests.
- **NOT YET VERIFIED:** execution/build behavior on Windows, macOS, Linux and mobile targets; generated Telegram bot against a real bot token; packaging and runtime acceptance beyond the browser prototype.
- **NOT SUPPORTED:** general reasoning across arbitrary unfamiliar codebases; guaranteed repair of every bug; fully featured OBS/media player/mobile app generation from a single prompt.
