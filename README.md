# BotPyton — deterministic GitHub worker without AI

BotPyton is a deterministic software-work automation bot. It uses **no AI models, inference APIs, or AI service credentials**. Its task engine combines a JSON queue with strict allowlists. Task descriptions are data, never commands.

## Capabilities

- JSON task queue with explicit states, dependency checks, idempotency, bounded attempts and interruption recovery.
- Cross-run queue checkpoints in GitHub Actions artifacts, with SHA-256 integrity checks.
- Repository inventory, syntax checks, fixed unit-test execution and JSON execution reports.
- Bounded autonomous cycle: run fixed tests, apply at most one exact known repair after a failure, rerun tests, verify Python/JSON files, and persist per-attempt evidence.
- Conservative diagnostics plus two exact source repairs: a uniquely located JSON trailing comma and a supported one-line Python compound-header colon.
- Declarative GitHub operations: create an isolated bot branch, create one commit containing the declared paths, and open a draft pull request for review.
- No automatic merge, no arbitrary shell commands, no automatic package installation, and no AI/API calls to AI services.

## Interfaz de escritorio

La forma más sencilla de usar BOTPYTON es **hacer doble clic en `BOTPYTON.bat`**. Se abre una pantalla simple: no hace falta conocer comandos ni interpretar informes de programación.

- **Analizar mi PC:** encadena ocho comprobaciones locales en serie: estado general, recursos básicos, uso de memoria, programas de inicio, aplicaciones instaladas, discos, controladores y temporales antiguos.
- La ventana muestra el nombre de cada etapa en lenguaje común y resume los resultados. Los comandos y los informes originales quedan escondidos tras **Ver detalle técnico**.
- El análisis es de solo lectura: no elimina archivos, no cierra programas, no instala controladores, no consulta actualizaciones por Internet y no cambia configuraciones.
- **Revisar archivos** solo crea una vista previa. Para elegir archivos individualmente, ponerlos en cuarentena reversible o restaurarlos, abrí **Más herramientas** en la pantalla simple o ejecutá `python -m bot.gui`.
- **BOTPYTON-CLI.bat** conserva el menú de texto para quien lo prefiera. Las tareas de proyecto, cola, pruebas, auditoría y otras funciones avanzadas siguen disponibles en la interfaz completa.
- La aplicación se inicia a demanda, ejecuta una operación a la vez y no instala servicios ni se agrega al inicio de Windows. Las comprobaciones se ejecutan en serie para evitar trabajo paralelo innecesario.
- Requiere Python 3.11+ con Tk/Tcl habilitado. Usa la biblioteca estándar, sin dependencias de interfaz externas ni servicios de IA.
- Es una revisión técnica amplia, no un antivirus, una búsqueda exhaustiva de cada archivo del disco ni una reparación automática. El resultado puede indicar límites si Windows no permite consultar algún dato.

## Quick start

Requires Python 3.11+.

```bash
python -m bot.main --check
python -m unittest discover -s tests -v
python -m bot.main --validate-task .cerebro/examples/example-task.json
python -m bot.main --plan .cerebro/examples/example-plan.json
```

## Queue and worker

Queue state defaults to `.cerebro/memory/state.json`; pass `--memory PATH` to choose another file.

```bash
python -m bot.main --enqueue .cerebro/examples/worker-task.json
python -m bot.main --queue
python -m bot.main --work-once
python -m bot.main --work-queue 10
python -m bot.main --resume
```

The worker supports the fixed actions `inspect_repository`, `validate_task`, `run_tests`, `apply_known_fix` and `prepare_report`. The unit-test action always runs `python -m unittest discover -s tests -v`, with `shell=False` and a timeout.

## Phase 05: known fixes

Rules in `bot/fixes.py` remove a single trailing comma from one invalid JSON file, or add a missing colon only when Python reports `SyntaxError: expected ':'` on a supported one-line `if`, `elif`, `for`, `while`, `def` or `class` header. Both rules require one uniquely identified broken file, parser verification and a timestamped copy under `.cerebro/backups/`. Unknown or ambiguous failures are never automatically edited. `.github/`, configuration, policy, memory, report and backup paths are protected.

## Phase 06: branch, commit and draft pull request

GitHub operations live in `bot/github_ops.py`. The JSON change plan is validated before any GitHub request. It has a strict path allowlist (`.py`, `.md`, `.json`, `.toml`, `.txt`), rejects duplicate/traversal/protected paths, and is bounded to 20 files, 200 KB per file and 500 KB total.

Local or CI dry-run (no API requests, no token required; set repository identity):

```bash
GITHUB_REPOSITORY=owner/repo python -m bot.main --github-plan .cerebro/examples/github-change-plan.json
```

To execute real GitHub operations, the caller must explicitly supply `--execute-github-plan` and a repository-scoped `GITHUB_TOKEN`:

```bash
python -m bot.main --github-plan .cerebro/examples/github-change-plan.json --execute-github-plan
```

Actual submission performs these steps:

1. Confirm the plan targets the repository's default branch.
2. Generate a deterministic `botpyton/<task>-<fingerprint>` branch name and create the branch from the current default-branch tip if absent.
3. Create a Git tree and one commit containing only the declared allowed files, then update that branch without force-pushing.
4. Open a draft pull request; if the same branch/plan already has an open PR, return it instead of creating a duplicate.

Repeated submissions of the same plan are idempotent. If a pre-existing deterministic branch has content that does not match the proposed plan, the operation stops rather than overwriting it. A closed prior PR is not duplicated. The engine contains no merge function.

### Manual workflow

The dedicated workflow is `.github/workflows/botpyton-github-ops.yml`. It is triggered by pushes to the phase branch for read-only CI and has a separate manual-submit job. The job that can write to GitHub runs **only** for an explicit `workflow_dispatch` with `execute=true`.

- Default mode: validate and print a dry-run summary.
- Execute mode: create a bot branch, one commit and a draft PR.
- Read-only validation job: `contents: read`.
- Actual submission job: `contents: write` and `pull-requests: write`, only; no actions-write permission.
- The workflow runs with `GITHUB_TOKEN` provided by Actions. No PAT or AI-service secret is needed.
- The workflow must first exist on the repository's default branch before GitHub exposes the manual dispatch control. This phase branch has not been merged automatically.

Example plan: `.cerebro/examples/github-change-plan.json`. It only proposes adding a documentation example; actual submission is disabled by default.

## Phase 04: cross-run queue persistence

The dedicated worker `.github/workflows/botpyton-worker.yml` uses GitHub Actions artifacts to carry queue state between runs of the same branch. It verifies the manifest, SHA-256 digest, task fingerprints, status values, retry bounds and dependency graph before restore. If a prior runner disappears with tasks marked `running`, the next run attempts bounded recovery.

Checkpoint retention is configured to request up to 90 days, subject to GitHub policies; it is not indefinite archival storage.

## Phase 07: bounded autonomous repair cycle

Run this from the repository root:

```bash
python -m bot.main --autonomous-cycle
python -m bot.main --autonomous-cycle --cycle-max-attempts 2 --cycle-timeout 60
```

The cycle runs only the fixed command `python -m unittest discover -s tests -v` with `shell=False`. Each invocation is bounded to 1–3 test attempts and 1–120 seconds per attempt. After a failed test run, it may use the existing exact-pattern repair rules in `bot/fixes.py` once; the original file is backed up and the candidate must pass parser verification. After repairs, the full Python syntax and JSON-file checks must pass before the test suite is rerun. The final successful state requires both tests and static verification to pass.

An unsupported/ambiguous failure, timeout, failed post-repair verification, or exhausted limit stops the cycle. It does not retry blindly and does not make an unverified repair after the final permitted test attempt. Every attempt has a separate JSON record under `.cerebro/reports/autonomous-cycles/<cycle-id>/attempt-NNN.json`; `summary.json` is updated atomically and includes the outcome, attempt list, and explicit no-AI flags. Records are persisted before the test starts and updated before/after repair work so interrupted cycles leave evidence. The normal CI invokes the cycle and validates its successful audit record; reports are uploaded as a CI artifact.

The test subprocess receives a filtered environment that removes common credential-bearing variable names. The cycle makes no network/API calls, does not install dependencies, does not merge branches, and never executes commands supplied in JSON or task text.

## Phase 08: JSON task orchestration from GitHub

The persistent worker workflow at .github/workflows/botpyton-worker.yml accepts a bounded JSON task batch and a separate batch of GitHub change plans from workflow_dispatch. These inputs are JSON data; task descriptions are never interpreted as shell commands.

### Validate task batches locally

~~~bash
python -m bot.main --validate-task-batch .cerebro/examples/github-task-batch.json
python -m bot.main --enqueue .cerebro/examples/github-task-batch.json --memory .cerebro/memory/state.json
python -m bot.main --work-queue 10 --memory .cerebro/memory/state.json
~~~

Task batch validation checks each task against the fixed action allowlist, rejects duplicate IDs and internal dependency cycles, and imposes a maximum of 25 tasks and 50,000 input bytes. References to earlier persisted tasks are allowed at batch-validation time; TaskQueue.enqueue_many then checks dependency existence and cycles atomically against the complete queue before persisting any incoming task. Work per dispatch is limited to 1–100 queue claims, and each task's max_attempts is separately limited to 1–3.

The worker restores the most recent retained botpyton-queue-checkpoint from a previous completed run of the same workflow and branch, validates its SHA-256 manifest, resumes interrupted work, and uploads the next checkpoint. GitHub Actions artifacts are branch-scoped in this workflow and have a configured/requested 90-day retention period; they are not indefinite archival storage.

### Draft pull requests from a declarative plan batch

Validate a batch without making GitHub API calls (the repository identity must be configured for the dry-run summary):

~~~bash
GITHUB_REPOSITORY=owner/repo python -m bot.main --github-plans .cerebro/examples/github-change-plan-batch.json
~~~

Real branch/commit/PR operations require an explicit opt-in and a suitable repository-scoped GITHUB_TOKEN:

~~~bash
python -m bot.main --github-plans .cerebro/examples/github-change-plan-batch.json --execute-github-plans
~~~

Plan validation happens for the entire batch before the first GitHub write. A batch may contain up to 10 plans and the input is limited to 50,000 bytes. Every plan must set draft: true; path, extension, size and protected-location rules use the Phase 06 policy. The submit loop is sequential, branch names are deterministic, and existing mismatched changes are rejected. If a later GitHub API call fails after earlier plans were submitted, those earlier draft PRs remain open for human review; there is no rollback or merge operation.

### GitHub Actions dispatch

Use the BotPyton persistent worker workflow and select the branch containing this phase. Supply tasks_json as one task object or an array, and change_plans_json as an array. Both are validated before queue access. work_limit must be 1–100. execute_prs defaults to false; when false, change plans are validated and summarized in dry-run mode only. Set it to true to enable the separate submit job.

The write job has only contents: write and pull-requests: write, and runs only for an explicit manual dispatch with execute_prs=true, after the validation and worker jobs succeed. The normal queue job has read-only repository permissions plus actions-read for checkpoints. There is no automatic merge, no AI/LLM service, and no AI API request. The workflow must be present on the repository's default branch for GitHub to expose workflow_dispatch; this development branch has not been merged automatically.

Examples: .cerebro/examples/github-task-batch.json and .cerebro/examples/github-change-plan-batch.json.

## Phase 09 / I-01: privileged PR sender isolation (fail-closed)

The two privileged submission jobs in `.github/workflows/botpyton-worker.yml` and `.github/workflows/botpyton-github-ops.yml` are deliberately disabled with `if: false`. Their former design checked out the selected workflow ref and executed repository Python code while exposing a write-capable token. A condition inside a workflow is not, by itself, an enforceable trust boundary when a caller can select a ref whose workflow definition may be changed.

Live PR submission remains disabled until repository administrators verify an independent trusted-code boundary and effective repository controls (branch protection/rulesets and, where used, required environment reviewers). Those controls were not established by this code change. Do not enable either job by changing its condition alone. No live submission was performed as part of I-01.

The common `validate_change_plan()` validator now rejects `draft: false`, so individual and batch paths share the draft-only policy. The batch validator continues to validate the complete batch before any write operation.

## Security model

- Python standard library only; no AI/LLM services or inference APIs.
- GitHub REST operations are restricted to the fixed `api.github.com` host and the configured repository.
- No arbitrary shell command from task text.
- Source-change plans are path-validated, size-limited, fingerprinted and committed on isolated branches.
- Each proposed PR is a draft by default and requires human review.
- No automatic merge, no force-push, no secret administration, and no write permissions in the normal test workflow.

## Current status

**PHASE 08 — GITHUB JSON TASK ORCHESTRATION / VERIFIED ON DEVELOPMENT BRANCH.** The phase branch passed the foundation test workflow (71 unit tests plus the autonomous-cycle and audit-record checks) and the persistent worker workflow (checkpoint restored, queue work completed, next checkpoint uploaded). The change remains isolated from main; live draft-PR submission was not executed. Any promotion still requires human review.


## Phase 11: on-demand local use and bounded repository inventory

BOTPYTON is intended to run **only when the user opens it**. The optional Windows launcher `BOTPYTON.bat` does not install a service, register a startup task, or keep a background process alive after the window closes. It offers a read-only project inventory, a core check, and an explicit choice to run the test suite. Python must already be installed and available as `python` on PATH.

Inspect a project without executing its code:

```bash
python -m bot.main --inspect-repository "C:\\path\\to\\project"
```

The inventory has bounds for file count, directory depth, and oversized files; skips common generated/dependency folders and symlinks; recognizes common language extensions and project manifests; and reports possible checks as suggestions only. It does not read source-file contents, install dependencies, run candidate commands, use the network, or call AI services. The default limits are 5,000 files, depth 8, and 2 MB per file for oversized-file reporting. An inventory marked `inventory_complete_with_limits` is partial and must not be treated as exhaustive.

The inventory is an initial capability, not a complete local-PC agent: it does not yet edit arbitrary projects or launch arbitrary tools. Any future execution/editing capability must be separately permissioned and bounded. The educational project catalog is in `docs/EDUCATIONAL-PROJECT-LIBRARY.json`; entries marked planned are ideas, not implemented applications.


## Tool discovery and local changes (Phase 11)

List developer tools found on the current PATH without executing them:

```bash
python -m bot.main --tools
python -m bot.main --probe-tool git
```

Only the second command runs a fixed, source-registered version probe. It does not accept arbitrary command arguments.

Local file-change plans are dry-run by default:

```bash
python -m bot.main --local-plan "C:\\path\\plan.json" --project-root "C:\\path\\project"
```

To apply a plan, add `--execute-local-plan`. Existing files require a matching `expected_sha256`; changes are limited to 20 files, 200 KB per file and 500 KB of total content. Existing files are backed up outside the project root. Protected paths, traversal and symlink path components are rejected. See `docs/LOCAL-CHANGE-PLANS.md` for the schema and protections.

The process runner uses separate arguments, an explicit executable allowlist, `shell=False`, a timeout, disconnected stdin, stripped credential-like environment variables and bounded returned output. It is not a general shell and does not execute commands discovered in repository text. These controls are foundations for the worker, not a claim that free-form programming tasks can already be autonomously solved.


Local change plans can also be submitted as queue tasks using the allowlisted action `apply_local_plan`; the plan `task_id` must match the queue task `id`. This routes structured edits through the same deterministic worker and report pipeline instead of bypassing it. The goal text is treated as a label, not as executable instructions. Without an AI model, free-form requests still need an explicit plan or a deterministic procedure supported by the worker.


### Queue a structured local programming task

The Windows launcher now exposes queue actions: add a JSON task, inspect the queue, and execute one task at a time. The included example `.cerebro/examples/worker-local-change-task.json` creates `botpyton_demo_output/hello.py` only after the user explicitly runs the queued task.

CLI equivalent (run from the BOTPYTON repository root):

```bash
python -m bot.main --enqueue .cerebro/examples/worker-local-change-task.json
python -m bot.main --queue
python -m bot.main --work-once --project-root "C:\\path\\to\\project"
```

The queue stores durable state under `.cerebro/memory/state.json` by default. Tasks are JSON and actions are allowlisted. This is an auditable, deterministic work order, not natural-language AI reasoning. Review any task JSON before queueing it, especially tasks that contain `apply_local_plan`.


## Resource-aware operation

`python -m bot.main --system-profile` reports the current operating system, Python executable, CPU count, available physical RAM and free disk space using lightweight standard-library/system calls. The process runner chooses a conservative child memory budget from available RAM: at most 768 MB and generally no more than half of currently available memory. On Windows, the runner applies that budget through a Job Object and terminates child processes when the job closes. On other platforms the memory limit is reported as not enforced; timeout, serial execution and bounded output capture still apply.

Because the reported available RAM varies with what Windows and other applications are using, BOTPYTON calculates the budget at runtime rather than assuming the full 16 GB is available.


## Cross-language test/build profiles

`python -m bot.main --list-project-checks --project-root "C:\\path\\project"` detects supported check profiles without executing them. When the repository has been reviewed, a specific profile can be run with `--run-project-check ID --project-root PATH --execute-project-code`. The explicit flag is mandatory because test/build scripts can execute arbitrary project code. Current profiles cover Python unittest, npm test/build scripts, Cargo, Go, Maven, .NET, Composer and Rake/Bundler where the required manifest and tool exist. See `docs/PROJECT-CHECKS.md`.

The worker now supports both explicit local file-change plans and explicit project-check profiles as queue actions. It still does not translate arbitrary natural-language requests into arbitrary code on its own; that requires a structured plan or a known deterministic procedure.


### Local GitHub authentication

For local GitHub operations, BOTPYTON first uses `GITHUB_TOKEN` when explicitly supplied. If it is absent, it can read an existing authenticated GitHub CLI session through the fixed command `gh auth token --hostname github.com`. The token remains in process memory, is not written to project files, and is not included in reports. To use this path, install GitHub CLI and authenticate it yourself with `gh auth login`. GitHub writes remain behind the existing explicit execution option; merging remains disabled.


## Investigación de errores no registrados

El diagnóstico no depende exclusivamente de que un mensaje esté en la tabla de errores conocidos. Cuando no hay coincidencia exacta, marca el caso como `unknown`, extrae de forma acotada el último mensaje y los frames del traceback, lee unas pocas líneas alrededor de las ubicaciones que estén dentro del proyecto y propone un siguiente paso verificable. No modifica el código ni ejecuta comandos durante esta investigación. El ciclo autónomo existente aplica únicamente reparaciones exactas conocidas y se detiene ante fallos desconocidos o ambiguos; ampliar ese ciclo requiere nuevas estrategias con pruebas de regresión.


## Isolated local workspaces

For larger changes, create a dedicated local Git worktree instead of editing the original checkout directly:

```bash
python -m bot.main --create-workspace TASK-123 --project-root "C:\\path\\to\\clean-repository"
python -m bot.main --workspace-status "C:\\path\\to\\workspace"
```

The source repository must be clean and committed. BOTPYTON creates a local branch/worktree outside the repository, disables Git hooks during worktree checkout, and does not push, create a PR or merge. Run queued tasks with `--work-once --project-root WORKSPACE_PATH` to target the isolated worktree. See `docs/WORKSPACES.md`.


## Verifiable task completion

Tasks that edit files or run a project check must declare machine-verifiable `verification_checks`. The worker validates file existence/hash, Python syntax, bounded JSON validity, successful test/check results, local-plan application and report existence. A task is not marked successful when one of its configured checks fails. Human-readable `success_conditions` remain descriptive; the structured checks are the enforced acceptance criteria. See `docs/TASK-VERIFICATION.md`.


## Phase 12: PC Care — first read-only local audit

Run on demand:

```bash
python -m bot.main --pc-check
```

On Windows, this reports available physical RAM, free disk space, older files found in a bounded set of per-user temp/cache folders, and startup registration names. The audit is offline and uses only Python's standard library. It performs no deletion, registry edits, process termination, updates, installs, telemetry, or AI/API calls. A candidate cleanup size is an estimate, not a guarantee; driver/app update states remain explicitly not checked until an opt-in, trusted update source is implemented. See `docs/PC-CARE-ROADMAP.md`.


## Phase 13: process memory snapshot

On Windows, run `python -m bot.main --pc-processes` to list a bounded snapshot of processes sorted by working-set memory. It uses the built-in `tasklist` command with a five-second timeout, no shell, and no command-line capture. It does not stop or change processes. Memory usage is a momentary observation, not proof that an application is unnecessary. See `docs/PC-CARE-ROADMAP.md`.


## Phase 14: exact cleanup preview

\`python -m bot.main --pc-cleanup-preview\` lists exact old-file candidates in approved temporary directories. The output is read-only, bounded to 200 candidates, and excludes browser caches, cookies, sessions, downloads, personal documents, and system directories from the executable cleanup scope. Partial scans are explicitly marked. Nothing is moved or deleted by the preview.


## Phase 15: installed application inventory

On Windows, run \`python -m bot.main --pc-apps\` to list installed application names, publishers, display versions and install dates from uninstall registry metadata. The inventory is capped at 500 by default. It does not run uninstallers, change registry data, or determine update availability.


## Phase 16: reversible cleanup quarantine and restore

Run \`python -m bot.main --pc-cleanup-quarantine\` for a fresh scan. BOTPYTON prints the exact paths, requires the literal confirmation \`QUARANTINE OLD TEMP FILES\`, revalidates each candidate and moves supported files to \`%LOCALAPPDATA%\\BOTPYTON\\Quarantine\\session-...\\\` instead of permanently deleting them. A manifest records original locations and file metadata.

Restore with \`python -m bot.main --pc-cleanup-restore "PATH\\manifest.json"\` and type \`RESTORE BOTPYTON QUARANTINE\`. Execution additionally requires the source directory to resolve to the canonical per-user `%LOCALAPPDATA%\\Temp`; a misconfigured `TEMP` path pointing elsewhere is not accepted. Restoration checks that the original location remains within that approved temp directory and never overwrites an existing file. Partial scans are rejected; at most 100 files are handled in one operation. Cross-volume, locked, changed, inaccessible and linked files are skipped/reported. This recovery feature has automated tests using temporary test directories; it has not been run on the user's PC. See \`docs/PC-CARE-ROADMAP.md\`.


## Phase 17: comprobación optativa de actualizaciones de aplicaciones

Ejecuta manualmente \`python -m bot.main --pc-update-check\` o elige N en \`BOTPYTON.bat\`. Esta opción puede utilizar la red, pero solo lista las aplicaciones para las que WinGet informa una actualización disponible. Antes de consultar el origen, BOTPYTON valida que el alias de WinGet corresponda al WindowsApps del usuario y que la fuente \`winget\` esté marcada como fiable y apunte exactamente a \`https://cdn.winget.microsoft.com/cache\`.

El único comando de consulta es \`winget list --upgrade-available --source winget --disable-interactivity\`, con timeout de 30 segundos y salida limitada. BOTPYTON no ejecuta \`winget upgrade\`, \`winget install\`, \`--all\`, instaladores ni acepta acuerdos por el usuario. Si no puede verificar la fuente, se niega a consultar. Esto no comprueba controladores, Windows Update ni actualizaciones de Microsoft Store. La referencia del comando está en [Microsoft Learn: list](https://learn.microsoft.com/en-us/windows/package-manager/winget/list) y [Microsoft Learn: source](https://learn.microsoft.com/en-us/windows/package-manager/winget/source).


## Phase 18: integridad, vista previa de restauracion y auditoria local

Cada manifiesto nuevo de cuarentena guarda el hash SHA-256 y la marca de tiempo de alta precision de cada archivo, ademas de un checksum del propio manifiesto que se valida antes de la restauracion. BOTPYTON verifica el hash antes de restaurar; si el contenido cambio, falta o excede el presupuesto, se deja en cuarentena. El cálculo usa bloques de 256 KiB y limita los datos leídos a 512 MiB por operación.

Ejecuta \`python -m bot.main --pc-cleanup-restore-preview "PATH\\manifest.json"\` para comprobar integridad y conflictos de destino sin modificar nada. Ejecuta \`python -m bot.main --pc-audit\` para leer el registro local de operaciones. La auditoría registra tipos de operación, estado y contadores, no rutas exactas; está limitada a 200 registros mostrados, archivos/eventos y bytes acotados. Los hashes detectan cambios accidentales, pero no sustituyen una firma criptográfica ni una copia de seguridad externa. Revisa \`docs/PC-CARE-ROADMAP.md\` para los límites.


## Phase 19: paquete portátil y medicion puntual de recursos

Crear y validar un paquete fuente portátil con Python 3.11+:

\`\`\`powershell
python -m bot.main --package-portable ".\\dist"
python -m bot.main --verify-portable-package ".\\dist\\BOTPYTON-portable.zip"
python -m bot.main --pc-benchmark
\`\`\`

El ZIP incluye los módulos, documentación y ejemplos, más un manifiesto con SHA-256 de cada miembro y un archivo lateral \`.sha256\`. La verificación comprueba miembros duplicados/inseguros, rutas, CRC, límites y hashes. No incluye un intérprete Python ni es un EXE autónomo; tampoco incluye estado local, reportes, caches, repositorio Git o tests.

\`--pc-benchmark\` hace una sola pasada de diagnóstico local con límites de escaneo, tiempos CPU/pared por operación y memoria residente actual/pico cuando el sistema lo permite. No modifica el PC, no limpia, no actualiza y no usa red. Los números de CI verifican el programa en runners; los valores reales de tu Ryzen y el pico de RAM todavía deben medirse en tu equipo.


## Phase 20: analisis de almacenamiento seguro

\`python -m bot.main --pc-storage "RUTA_DE_CARPETA"\` o la opción R del menú analiza una carpeta que elijas. Informa bytes observados, número de archivos/directorios, archivos más grandes y resumen por extensión. No abre contenidos ni cambia nada.

La pasada tiene límites estrictos: 5.000 entradas, profundidad 6, top 20 y cinco segundos por defecto; máximo absoluto 20.000 entradas, profundidad 8, top 50 y diez segundos. Omite enlaces y puntos de reanálisis, y marca resultados parciales cuando llega a un límite o encuentra errores. No analiza toda la unidad de forma automática; debes escoger la carpeta. El informe contiene rutas locales, así que revísalo antes de compartirlo.


## Phase 21: buscador de duplicados sin eliminacion automatica

\`python -m bot.main --pc-duplicates "RUTA_DE_CARPETA"\` o la opción S busca archivos de contenido idéntico dentro de una carpeta que selecciones. Agrupa por tamaño antes de calcular SHA-256 en bloques de 256 KiB. Límites por defecto: 5.000 entradas, profundidad 6, cinco segundos, 32 MiB máximo por archivo y 256 MiB de lectura total para hashes. Los límites duros son 20.000 entradas, profundidad 8, diez segundos y 2.000 hashes.

La salida estima el espacio potencialmente duplicado, pero no elimina archivos: puede haber hard links, archivos en uso o diferencias entre espacio estimado y realmente recuperable. Los contenidos se leen localmente solo para calcular hashes; no se envían a ningún servicio. Si se alcanza un límite, se informa un resultado parcial. Los informes contienen rutas locales.


## Phase 22: inventario local de controladores

Ejecuta \`python -m bot.main --pc-drivers\` o elige T en el menú. Obtiene del proveedor local de Windows una lista limitada de controladores Plug-and-Play: dispositivo, clase, fabricante/proveedor, versión, fecha, estado de firma e INF. Omite identificadores de dispositivo. La consulta utiliza PowerShell local con timeout de 15 segundos, límite de 200 registros y sin shell ni red.

Esto es solo inventario. Una fecha antigua no demuestra que el controlador esté obsoleto, y esta fase no consulta catálogos de fabricantes ni descarga, instala, quita o desactiva controladores.


## Phase 23 — Local disk status snapshot (Windows)

Run python -m bot.main --pc-disk-health or choose **U** in BOTPYTON.bat.

BOTPYTON asks the local Windows PowerShell providers Get-PhysicalDisk and Win32_DiskDrive for bounded device/status metadata. It reports the health and operational status that Windows exposes, plus basic disk/interface metadata. Each provider is capped at 100 records, output is capped, the command has a 15-second timeout and uses no shell or network connection.

This is **not** a complete S.M.A.R.T. attribute reader, and an OK, Healthy, blank, or missing result is not a guarantee of drive health. Some USB bridges, RAID controllers, virtual disks and storage drivers do not expose the same fields. If Windows reports a warning or the drive behaves abnormally, use the drive manufacturer's diagnostic and maintain a current backup. The feature does not run repair, CHKDSK, formatting, optimization, driver updates or other disk-changing operations. The Windows smoke workflow exercises the command; the actual target PC still needs a local run.


## Phase 24 — Read-only doctor / actionable preflight

Run `python -m bot.main --doctor` or choose **V** in `BOTPYTON.bat`. It checks the Python minimum version, required BOTPYTON core files, available RAM, free disk space, Git availability and (on Windows) the standard PowerShell path. Each warning/block includes a concrete next step; it explicitly tells the operator to stop rather than guess when core files are missing.

The doctor is local, deterministic, and read-only. It does not install packages, launch tests/builds, access the network, edit files, change settings, or attempt automatic repair. A low-memory result advises running one bounded operation at a time rather than starting heavier tasks. Exit code 0 means ready or caution; exit code 2 means a blocking prerequisite was detected. This is a preflight, not a guarantee that every later operation will succeed.


## Phase 25 — Fail-closed PC-care result contract

- PC-care inventory commands attach `operation_assessment` to their JSON output and return a non-zero exit code for partial, failed, or unknown results instead of silently returning success.
- The guard checks known status values, provider return codes, returned-record counts, integrity status, scan-limit flags, truncated output, access errors and skipped restore/quarantine actions. It never retries automatically.
- Exit codes: `0` = recognized structurally valid result; `1` = failure/postcondition mismatch; `2` = partial/incomplete result; `3` = unknown report/status. A result marked `reported_success` is not a claim that hardware is healthy or that every OS provider exposes all devices.
- Tests cover success, partial scans, provider failure, count mismatch, integrity failure, unknown status and malformed output. CI must pass on the exact commit before this phase is verified.



## Competitive scope audit

The current feature gap against CCleaner is documented in [`docs/CCLEANER-PARITY-AUDIT.md`](docs/CCLEANER-PARITY-AUDIT.md). BOTPYTON is **not yet feature-equivalent**: it prioritizes local/offline diagnostics, bounded scans and reversible actions, while CCleaner offers broader browser/application cleaning, startup controls, software and driver updating, background optimization, and scheduled/real-time features depending on edition. Do not market BOTPYTON as faster or more capable until feature and target-PC measurements support that claim.


## Phase 27 — Read-only startup target advisor

Run `python -m bot.main --pc-startup-advisor` or select **W** in `BOTPYTON.bat`. The advisor reads Windows Run/RunOnce entries and startup-folder items, extracts only a recognized executable/script target, checks whether that target exists, and never returns registry command arguments. It does not disable entries, launch commands, measure actual boot impact, or label a program unnecessary. Results can include local paths; inspect them before sharing.
