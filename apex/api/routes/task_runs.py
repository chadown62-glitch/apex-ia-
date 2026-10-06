"""Exécuter une tâche pour de vrai et la suivre : `POST /tasks/{id}/runs`, `GET /task-runs/{id}`.

Point d'entrée produit de la boucle existante `apex.taskloop.loop.run_task_loop` (via
`apex.taskloop.runs.TaskRunService`) — avant l'audit du 03/10/2026, aucun : la boucle n'était appelée
que par des tests. Chaque donnée renvoyée vient de la base : la TaskRun, ses `EvalRun` (code RÉELLEMENT
généré, exécution, verdict) et ses `Experience` — jamais reconstruite ni estimée.

`POST /tasks/reference` crée la tâche de référence existante (`apex.tasks.solution_tasks`,
`is_palindrome` : énoncé montré au modèle, tests cachés jamais montrés, réponses attendues jamais
envoyées dans la sandbox) — l'écriture libre de tâches (harnais fourni par l'utilisateur) reste à
concevoir."""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apex.api.schemas import DataEnvelope

router = APIRouter(tags=["task-runs"])



class ReferenceTaskIn(BaseModel):
    kind: str = Field(default="is_palindrome")


class FunctionCaseIn(BaseModel):
    args: list[Any] = Field(default_factory=list)
    expected: Any = None


class FunctionTaskIn(BaseModel):
    """Tâche définie par l'utilisateur : énoncé, nom de fonction, cas cachés. Aucun code fourni par
    l'utilisateur n'est exécuté : APEX écrit le harnais (`apex.tasks.solution_tasks`)."""

    title: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    function: str = Field(min_length=1, max_length=64)
    cases: list[FunctionCaseIn] = Field(min_length=1, max_length=50)


class ProjectCaseIn(BaseModel):
    module: str = Field(min_length=1, max_length=200)
    function: str = Field(min_length=1, max_length=64)
    args: list[Any] = Field(default_factory=list)
    expected: Any = None


class ProjectTaskIn(BaseModel):
    """Projet Python à plusieurs fichiers : fichiers de départ VISIBLES et vérifications cachées
    `module:fonction(args)` (valeurs attendues jamais montrées). Exécuté en mode outillé uniquement."""

    title: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    seed_files: dict[str, str] = Field(default_factory=dict)
    cases: list[ProjectCaseIn] = Field(min_length=1, max_length=50)


class TaskRunIn(BaseModel):
    objective: str = Field(default="résoudre la tâche", min_length=1, max_length=2000)
    execution_mode: str = Field(default="bounded")
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_candidates: int = Field(default=2, ge=1, le=5)
    max_wall_clock_s: float = Field(default=900.0, gt=0, le=3600)
    generation_timeout_s: float = Field(default=120.0, gt=0, le=900)
    #: TaskRun TERMINÉE de la même tâche dont les Experience conditionnent la stratégie (None : baseline).
    condition_on_run: str | None = Field(default=None, max_length=36)
    #: Protocole v2 : `rule` (bras B) ou `failure_facts` (bras C) ; et température de base explicite.
    strategy: str = Field(default="rule", max_length=32)
    base_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    #: Entre deux tentatives : `failure_facts` (faits d'échec autorisés de la tentative précédente) ou `none`.
    retry_adaptation: str = Field(default="failure_facts", max_length=32)
    #: Étape PLAN réelle (analyse et plan rédigés par le modèle avant la génération).
    plan_first: bool = False
    #: Mode outillé : permissions EXPLICITEMENT accordées (`workspace.read`, `workspace.write`,
    #: `sandbox.execute`) ; vide : une tentative = un appel au modèle.
    tool_permissions: list[str] = Field(default_factory=list, max_length=3)
    max_tool_steps: int = Field(default=8, ge=1, le=30)
    #: Joindre au prompt les souvenirs SYSTÈME les plus pertinents (exécutions passées, documents, projets).
    use_memory: bool = False


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _eval_run_view(run: Any) -> dict[str, Any]:
    facts = run.facts or {}
    response = facts.get("model_response") or {}
    execution = facts.get("candidate_execution") or {}
    from apex.experience.failure_facts import extract_failure_facts
    from apex.experience.failure_taxonomy import classify_failure

    category = classify_failure(run)
    failure_facts = extract_failure_facts(run, category)
    return {
        "id": run.id,
        "failure_category": category.value,
        # Faits AUTORISÉS (liste fermée, apex.metalearner.failure_facts) — jamais messages ni cas cachés.
        "failure_facts": failure_facts.to_dict() if failure_facts else None,
        "model_id": run.model_id,
        "oracle_verdict": _plain(run.oracle_verdict),
        "oracle_detail": run.oracle_detail,
        "sandbox_status": _plain(run.sandbox_status),
        "exit_code": run.exit_code,
        "elapsed_s": run.elapsed_s,
        "isolation": facts.get("isolation_level"),
        "isolation_detail": facts.get("isolation_detail"),
        "container_image": facts.get("image"),
        "generated_code": response.get("text"),
        "generated_code_truncated": response.get("truncated"),
        "tokens_completion": response.get("tokens_completion"),
        "generation_elapsed_s": response.get("elapsed_s"),
        "backend_timings_s": response.get("backend_timings_s"),
        "stdout": execution.get("stdout"),
        "stderr": execution.get("stderr"),
        "started_at": _plain(run.started_at),
        "completed_at": _plain(run.completed_at),
    }


def _detail(run: Any, session_factory: Any) -> dict[str, Any]:
    from apex.evalrunner.repository import EvalRunRepository
    from apex.experience.store import ExperienceRepository

    eval_repo = EvalRunRepository(session_factory)
    eval_runs = [r for r in (eval_repo.get(i) for i in run.eval_run_ids) if r is not None]
    wanted = set(run.eval_run_ids)
    experiences = [
        _plain(asdict(e))
        for e in ExperienceRepository(session_factory).list_by_task(run.task_id, limit=500)
        if e.eval_run_id in wanted
    ]
    return {
        **run.to_dict(),
        "eval_runs": [_eval_run_view(r) for r in eval_runs],
        "experiences": experiences,
        "events": _trace(run, session_factory),
    }


def _trace(run: Any, session_factory: Any) -> list[dict[str, Any]]:
    """Trace RÉELLE de l'exécution (événements `apex.taskloop` portant son `trace_id`), dans l'ordre."""
    from datetime import timedelta

    from apex.db.engine import session_scope
    from apex.db.repositories import EventRepository

    until = run.completed_at + timedelta(seconds=5) if run.completed_at else None
    with session_scope(session_factory) as session:
        rows = EventRepository(session).in_window(
            logger="apex.taskloop", since=run.started_at - timedelta(seconds=5), until=until
        )
        return [
            {"ts": row.ts.isoformat(), **row.context}
            for row in rows
            if (row.context or {}).get("trace_id") == run.id
        ]


@router.get("/tasks/reference/kinds", response_model=DataEnvelope[list[str]])
def list_reference_task_kinds() -> DataEnvelope[list[str]]:
    from apex.tasks.solution_tasks import REFERENCE_TASK_KINDS

    return DataEnvelope(status="real", data=list(REFERENCE_TASK_KINDS))


@router.post("/tasks/reference", response_model=DataEnvelope[dict[str, Any]])
def create_reference_task(body: ReferenceTaskIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory
    from apex.api.routes.evals import _to_task_out
    from apex.policy.model import Subject
    from apex.tasks.solution_tasks import REFERENCE_TASK_KINDS, build_reference_task_spec
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService

    if body.kind not in REFERENCE_TASK_KINDS:
        raise HTTPException(status_code=422, detail=f"tâche de référence « {body.kind} » inconnue")
    spec = build_reference_task_spec(body.kind)
    try:
        task = TaskBankService(get_session_factory()).create(
            split=TaskSplit.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=Subject.HUMAN,
            solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_task_out(task).model_dump())


@router.post("/tasks/function", response_model=DataEnvelope[dict[str, Any]])
def create_function_task(body: FunctionTaskIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory
    from apex.api.routes.evals import _to_task_out
    from apex.policy.model import Subject
    from apex.tasks.solution_tasks import InvalidUserTask, build_user_function_task_spec
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService

    try:
        spec = build_user_function_task_spec(
            function=body.function, description=body.description, title=body.title,
            cases=[c.model_dump() for c in body.cases],
        )
    except InvalidUserTask as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        task = TaskBankService(get_session_factory()).create(
            split=TaskSplit.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=Subject.HUMAN,
            solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_task_out(task).model_dump())


@router.post("/tasks/project", response_model=DataEnvelope[dict[str, Any]])
def create_project_task(body: ProjectTaskIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory
    from apex.api.routes.evals import _to_task_out
    from apex.policy.model import Subject
    from apex.tasks.model import TaskKind, TaskSplit
    from apex.tasks.service import TaskBankService
    from apex.tasks.solution_tasks import InvalidUserTask, build_user_project_task_spec

    try:
        spec = build_user_project_task_spec(
            description=body.description, seed_files=body.seed_files, title=body.title,
            cases=[c.model_dump() for c in body.cases],
        )
    except InvalidUserTask as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        task = TaskBankService(get_session_factory()).create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer, kind=TaskKind.PROJECT, workspace_seed=spec.workspace_seed,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_task_out(task).model_dump())


@router.post("/tasks/{task_id}/runs", response_model=DataEnvelope[dict[str, Any]], status_code=202)
def start_task_run(task_id: str, body: TaskRunIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_task_run_service
    from apex.taskloop.request import TaskExecutionMode, TaskLimits

    try:
        mode = TaskExecutionMode(body.execution_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"mode « {body.execution_mode} » inconnu") from exc
    try:
        service = get_task_run_service()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    try:
        run = service.start(
            task_id=task_id,
            objective=body.objective,
            execution_mode=mode,
            limits=TaskLimits(
                max_attempts=body.max_attempts,
                max_retries=max(body.max_attempts - 1, 0),
                max_candidates=body.max_candidates,
                max_wall_clock_s=body.max_wall_clock_s,
            ),
            generation_timeout_s=body.generation_timeout_s,
            condition_on_run=body.condition_on_run,
            strategy=body.strategy,
            base_temperature=body.base_temperature,
            retry_adaptation=body.retry_adaptation,
            plan_first=body.plan_first,
            tool_permissions=tuple(body.tool_permissions),
            max_tool_steps=body.max_tool_steps,
            use_memory=body.use_memory,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DataEnvelope(status="real", data=run.to_dict())


@router.get("/tasks/{task_id}/runs", response_model=DataEnvelope[list[dict[str, Any]]])
def list_task_runs(task_id: str) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.api.dependencies import get_task_run_service

    try:
        runs = get_task_run_service().repo.list_by_task(task_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[r.to_dict() for r in runs])


@router.get("/task-runs/{run_id}", response_model=DataEnvelope[dict[str, Any]])
def get_task_run(run_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory, get_task_run_service

    try:
        run = get_task_run_service().repo.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"exécution « {run_id} » introuvable")
        data = _detail(run, get_session_factory())
    except HTTPException:
        raise
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)
