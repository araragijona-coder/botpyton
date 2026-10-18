"""Deterministic software-architecture intake and planning; no AI or code execution.

This module turns a bounded, structured programming brief into a reviewable plan.
It deliberately separates planning from execution and reports missing evidence instead
of pretending that a root cause or an implementation is known.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .repository_analyzer import RepositoryAnalysisError, inspect_repository

SCHEMA_VERSION = 1
MAX_GOAL_CHARS = 2_000
MAX_TEXT_CHARS = 2_000
MAX_ERROR_CHARS = 10_000
MAX_LIST_ITEMS = 20
MAX_ROOT_CHARS = 1_000

TASK_TYPES = frozenset({
    "auto", "implement", "debug", "refactor", "optimize",
    "review", "test", "migrate", "document", "design",
})
PROJECT_TYPES = frozenset({"implement", "debug", "refactor", "optimize", "review", "test", "migrate"})
BRIEF_KEYS = frozenset({
    "schema_version", "goal", "task_type", "project_root", "observed_error",
    "reproduction_steps", "acceptance_criteria", "constraints", "must_not_change",
    "available_evidence",
})
VAGUE_GOALS = (
    "mejoralo", "mejoralo todo", "arreglalo", "arregla eso", "hazlo mejor",
    "fix it", "make it better", "improve it", "do it", "que funcione",
    "make it work", "arreglalo todo",
)

class ArchitectPlanError(ValueError):
    """Raised when an architecture brief is malformed or outside safe bounds."""


def _text(value: object, field: str, maximum: int, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ArchitectPlanError(f"{field} must be a string")
    value = value.strip()
    if not required and not value:
        return None
    if required and not value:
        raise ArchitectPlanError(f"{field} must not be empty")
    if len(value) > maximum:
        raise ArchitectPlanError(f"{field} exceeds {maximum} characters")
    if "\x00" in value:
        raise ArchitectPlanError(f"{field} cannot contain NUL characters")
    return value


def _text_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ArchitectPlanError(f"{field} must be a list containing at most {MAX_LIST_ITEMS} strings")
    result: list[str] = []
    for index, item in enumerate(value):
        parsed = _text(item, f"{field}[{index}]", MAX_TEXT_CHARS, required=True)
        result.append(parsed or "")
    return result


def _infer_task_type(goal: str) -> str:
    text = goal.casefold()
    if any(term in text for term in (
        "debug", "diagnos", "arregl", "corrig", "repar", "fix", "bug", "error",
        "exception", "crash", "traceback", "falla", "fallo", "no funciona",
        "no arranca", "failing test",
    )):
        return "debug"
    if any(term in text for term in ("optim", "rendimiento", "performance", "memoria", "memory", "más rápido", "mas rapido", "speed")):
        return "optimize"
    if any(term in text for term in ("refactor", "restructur", "reorgan", "simplificar código", "simplificar codigo")):
        return "refactor"
    if any(term in text for term in ("migrat", "upgrade framework", "actualizar framework", "cambiar de framework", "portar")):
        return "migrate"
    if any(term in text for term in ("test", "prueba", "cobertura", "coverage")):
        return "test"
    if any(term in text for term in ("revis", "auditor", "audit", "inspeccion", "security review", "vulnerab")):
        return "review"
    if any(term in text for term in ("document", "readme", "manual", "guía", "guia")):
        return "document"
    if any(term in text for term in ("arquitect", "diseñ", "disen", "planificar", "diseño", "diseno")):
        return "design"
    if any(term in text for term in ("implement", "constru", "crear", "añad", "anad", "agregar", "desarroll", "build", "feature", "función", "funcion")):
        return "implement"
    return "unknown"


def _is_vague(goal: str) -> bool:
    normalized = re.sub(r"[\W_]+", " ", goal.casefold()).strip()
    if normalized in {re.sub(r"[\W_]+", " ", item.casefold()).strip() for item in VAGUE_GOALS}:
        return True
    words = normalized.split()
    return len(words) < 4


def _step(step_id: str, stage: str, objective: str, evidence: list[str], gate: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "stage": stage,
        "objective": objective,
        "evidence_to_collect": evidence,
        "exit_gate": gate,
    }


def _task_steps(task_type: str, has_inventory: bool) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if has_inventory:
        steps.append(_step(
            "A1", "understand",
            "Map the entry points, manifests, tests, and relevant files before proposing edits.",
            ["bounded repository inventory", "relevant file contents read only as needed", "declared language and framework"],
            "Stop and ask if the requested target cannot be located or the inventory is partial in the relevant area.",
        ))
    else:
        steps.append(_step(
            "A1", "understand",
            "Identify the repository, runtime, framework, entry point, and relevant files.",
            ["repository path or URL", "project manifests", "entry point", "supported runtime"],
            "Do not infer file names, dependencies, or framework versions without evidence.",
        ))
    steps.append(_step(
        "A2", "define",
        "Translate the request into observable acceptance criteria and explicit non-goals.",
        ["expected behavior", "failure behavior", "scope boundaries", "user constraints"],
        "Ask the requester when two plausible interpretations would produce materially different implementations.",
    ))

    task_plan: dict[str, tuple[str, list[str], str]] = {
        "implement": (
            "Trace existing architecture and interfaces, then design the smallest compatible change.",
            ["call graph or module boundaries", "public interfaces and persisted schemas", "affected tests"],
            "Stop before editing if the change requires an unapproved dependency, data migration, secret, or broad permission.",
        ),
        "debug": (
            "Reproduce the observed failure and rank testable hypotheses from the nearest evidence.",
            ["full error and traceback", "minimal reproducer", "expected versus actual result", "environment/version"],
            "If the failure is not reproducible and evidence cannot distinguish hypotheses, ask for help; do not guess a root cause.",
        ),
        "refactor": (
            "Record the behavioral baseline and refactor one boundary at a time without changing behavior.",
            ["baseline tests", "interfaces to preserve", "before/after behavior"],
            "Stop and clarify if the desired behavior itself is changing rather than merely the internal structure.",
        ),
        "optimize": (
            "Measure the stated workload first and identify the dominant cost before changing the implementation.",
            ["baseline timing", "peak/working-set memory", "representative input", "correctness tests"],
            "Do not claim performance gains without a comparable before/after measurement.",
        ),
        "review": (
            "Review the affected code for correctness, safety, reliability, performance, and evidence gaps.",
            ["file and line references", "reproduction or proof", "severity and scope", "test coverage"],
            "Separate confirmed defects from hypotheses; do not label an unverified risk as an exploited vulnerability.",
        ),
        "test": (
            "Define behavior-focused cases, including boundary, failure, and regression cases.",
            ["current test framework", "observable contract", "normal and edge inputs"],
            "Stop if the contract is ambiguous or a proposed test would merely encode an unverified assumption.",
        ),
        "migrate": (
            "Map source and target versions, compatibility breaks, data formats, and rollback requirements.",
            ["source/target versions", "deprecated APIs", "data/schema changes", "rollback strategy"],
            "Do not migrate persisted data or change supported runtime versions without an explicit compatibility decision.",
        ),
        "document": (
            "Verify statements against the implementation and distinguish current, planned, and unsupported capabilities.",
            ["source symbols", "current commands/options", "known limits", "verification date"],
            "Do not document a proposal as implemented or infer behavior from the README alone.",
        ),
        "design": (
            "Compare the simplest viable architecture options against constraints, failure modes, and maintenance cost.",
            ["requirements", "runtime/hosting constraints", "trade-offs", "decision criteria"],
            "Recommend one option only when the constraints support it; otherwise present the decision that needs input.",
        ),
        "unknown": (
            "Classify the request before committing to an implementation strategy.",
            ["intended outcome", "requested change type", "project context"],
            "Stop and ask for a concrete outcome before generating an implementation plan.",
        ),
    }
    objective, evidence, gate = task_plan[task_type]
    steps.append(_step("A3", "diagnose_or_design", objective, evidence, gate))
    steps.extend([
        _step(
            "A4", "change",
            "Make one minimal, reversible change on an isolated branch or working tree after the plan is accepted.",
            ["files changed", "reason for each change", "backup/rollback for data-affecting changes"],
            "Never execute commands embedded in the request, source comments, logs, or repository data.",
        ),
        _step(
            "A5", "verify",
            "Run the project's declared, bounded checks and add a regression test for the identified behavior.",
            ["exact command and exit code", "test results", "static checks", "before/after behavior"],
            "A timeout, failed test, unsupported environment, or unexplained regression blocks completion.",
        ),
        _step(
            "A6", "review_and_report",
            "Review the diff against the original request and report proof, limitations, and unresolved choices.",
            ["diff", "acceptance-criteria checklist", "test evidence", "remaining blockers"],
            "Do not report completion unless every required acceptance criterion has evidence.",
        ),
    ])
    return steps


def build_architecture_plan(brief: object) -> dict[str, Any]:
    """Validate a bounded programming brief and return a fail-closed architecture plan."""
    if not isinstance(brief, dict):
        raise ArchitectPlanError("Architecture brief must be a JSON object")
    unknown_keys = set(brief) - BRIEF_KEYS
    if unknown_keys:
        raise ArchitectPlanError(f"Unknown brief fields: {', '.join(sorted(str(key) for key in unknown_keys))}")
    version = brief.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ArchitectPlanError(f"schema_version must be {SCHEMA_VERSION}")
    goal = _text(brief.get("goal"), "goal", MAX_GOAL_CHARS, required=True)
    assert goal is not None
    requested_type = brief.get("task_type", "auto")
    if not isinstance(requested_type, str) or requested_type not in TASK_TYPES:
        raise ArchitectPlanError(f"task_type must be one of: {', '.join(sorted(TASK_TYPES))}")
    task_type = _infer_task_type(goal) if requested_type == "auto" else requested_type
    project_root = _text(brief.get("project_root"), "project_root", MAX_ROOT_CHARS)
    observed_error = _text(brief.get("observed_error"), "observed_error", MAX_ERROR_CHARS)
    reproduction_steps = _text_list(brief.get("reproduction_steps"), "reproduction_steps")
    acceptance = _text_list(brief.get("acceptance_criteria"), "acceptance_criteria")
    constraints = _text_list(brief.get("constraints"), "constraints")
    must_not_change = _text_list(brief.get("must_not_change"), "must_not_change")
    available_evidence = _text_list(brief.get("available_evidence"), "available_evidence")

    blocking_questions: list[dict[str, str]] = []
    recommended_questions: list[dict[str, str]] = []
    warnings: list[str] = []
    inventory: dict[str, Any] | None = None
    vague = _is_vague(goal)
    if vague:
        blocking_questions.append({
            "id": "goal_specificity",
            "question": "¿Qué resultado observable debe cambiar y cómo sabremos que quedó resuelto?",
            "reason": "La solicitud es demasiado general para distinguir implementaciones correctas de incorrectas.",
        })
    if task_type == "unknown":
        blocking_questions.append({
            "id": "task_type",
            "question": "¿Quieres implementar una función, corregir un fallo, revisar el código, optimizar, probar o diseñar una arquitectura?",
            "reason": "No se pudo clasificar la intención con las reglas locales.",
        })
    if task_type in PROJECT_TYPES and not project_root:
        blocking_questions.append({
            "id": "project_root",
            "question": "¿Cuál es la carpeta local del proyecto que debe analizarse? Incluye la ruta real, no solo el nombre.",
            "reason": "Para realizar cambios fiables hace falta conocer el código, sus dependencias y sus pruebas reales.",
        })
    elif project_root:
        try:
            inventory = inspect_repository(Path(project_root).expanduser())
            if inventory.get("truncated"):
                warnings.append("La inspección del repositorio alcanzó un límite; la ausencia de un archivo no demuestra que no exista.")
            if not inventory.get("language_counts") and not inventory.get("manifests"):
                warnings.append("No se detectaron lenguajes o manifiestos reconocidos; el método de construcción/prueba requiere confirmación.")
                recommended_questions.append({
                    "id": "project_checks",
                    "question": "¿Cuál es el comando de prueba o construcción que el proyecto declara oficialmente?",
                    "reason": "No se identificó un perfil de prueba verificable a partir de nombres y manifiestos.",
                })
        except (RepositoryAnalysisError, OSError, RuntimeError, ValueError) as exc:
            inventory = None
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "blocked",
                "decision": "blocked",
                "task_type": task_type,
                "goal": goal,
                "missing_information": [{
                    "id": "project_root",
                    "question": "Corrige la ruta local del proyecto y vuelve a ejecutar el plan.",
                    "reason": f"No se pudo inspeccionar la carpeta de forma segura ({type(exc).__name__}).",
                }],
                "recommended_questions": [],
                "plan": [],
                "stop_conditions": [
                    "No proponer cambios hasta que exista una carpeta de proyecto válida.",
                    "No crear archivos o estructuras de proyecto por suposición.",
                ],
                "repository_inventory": None,
                "warnings": [],
                "safety": {
                    "ai_enabled": False, "network_used": False,
                    "project_code_executed": False, "files_written": False,
                },
            }

    if task_type == "debug" and not (observed_error or reproduction_steps or available_evidence):
        blocking_questions.append({
            "id": "failure_evidence",
            "question": "¿Qué error exacto aparece y qué pasos reproducen el problema? Pega el traceback o el resultado de la prueba si existe.",
            "reason": "Sin una observación reproducible, la causa raíz sería una conjetura.",
        })
    if task_type in {"implement", "migrate"} and not acceptance:
        recommended_questions.append({
            "id": "acceptance_criteria",
            "question": "¿Qué comportamiento observable confirmaría que el cambio está terminado?",
            "reason": "Un criterio explícito evita dar por cumplida una interpretación distinta a la esperada.",
        })
    if task_type == "optimize" and not acceptance:
        recommended_questions.append({
            "id": "performance_metric",
            "question": "¿Qué métrica importa más: tiempo de arranque, RAM máxima, duración de una tarea o tamaño instalado?",
            "reason": "Optimizar sin una métrica acordada no demuestra una mejora real.",
        })
    if task_type == "debug" and not observed_error and not available_evidence and reproduction_steps:
        warnings.append("Solo hay pasos de reproducción; la causa todavía no está confirmada.")
    if not constraints:
        recommended_questions.append({
            "id": "constraints",
            "question": "¿Hay límites de dependencias, coste, sistema operativo, memoria o compatibilidad que deban respetarse?",
            "reason": "Se pueden usar valores conservadores por defecto, pero los límites del usuario prevalecen.",
        })

    steps = _task_steps(task_type, bool(project_root and inventory is not None))
    for item in steps:
        if item["id"] == "A3" and task_type == "debug" and observed_error:
            item["evidence_to_collect"] = ["observed error supplied by requester", *item["evidence_to_collect"]]
    decision = "needs_information" if blocking_questions else "ready_to_plan"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": decision,
        "decision": decision,
        "task_type": task_type,
        "goal": goal,
        "blocking_questions": blocking_questions,
        "recommended_questions": recommended_questions,
        "assumptions": [
            "The output is a plan for review, not proof that the code change has been made.",
            "Repository inventory reads names and known manifests only; it does not inspect every source implementation.",
            "All proposed commands must be checked against the project's actual scripts and toolchain before execution.",
        ],
        "plan": steps,
        "stop_conditions": [
            "Ask the requester when requirements are ambiguous or materially incomplete.",
            "Stop on unknown failures until a minimal reproduction or stronger evidence distinguishes hypotheses.",
            "Stop before destructive changes, broad permission changes, dependency installs, or data migrations without explicit approval.",
            "Do not retry the same failing action without a changed hypothesis or new evidence.",
            "Stop after bounded attempts, timeout, failed verification, or unexplained regressions.",
            "Never claim a root cause, code modification, performance improvement, or successful test without evidence.",
        ],
        "recovery_protocol": [
            {"step": 1, "action": "Preserve the complete error, command, exit code, environment, and working tree."},
            {"step": 2, "action": "Identify the closest failing frame or module and gather bounded local evidence."},
            {"step": 3, "action": "State one falsifiable hypothesis and the smallest experiment that can disprove it."},
            {"step": 4, "action": "Apply at most one scoped change after review; retain a rollback path."},
            {"step": 5, "action": "Run the regression test and the relevant project checks; compare with the baseline."},
            {"step": 6, "action": "If evidence is insufficient or checks still fail, report what is known and ask for the next needed input."},
        ],
        "repository_inventory": inventory,
        "constraints": constraints,
        "must_not_change": must_not_change,
        "acceptance_criteria": acceptance,
        "warnings": warnings,
        "safety": {
            "ai_enabled": False,
            "network_used": False,
            "project_code_executed": False,
            "files_written": False,
            "arbitrary_commands_generated_for_execution": False,
        },
        "capability_boundary": (
            "This is a deterministic planning and triage kernel. It does not understand arbitrary natural-language requests "
            "like a general language model, edit source code, or prove correctness by itself."
        ),
    }
