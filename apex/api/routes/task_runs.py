"""Exécuter une tâche pour de vrai et la suivre : `POST /tasks/{id}/runs`, `GET /task-runs/{id}`.

Point d'entrée produit de la boucle existante `apex.taskloop.loop.run_task_loop` (via
`apex.taskloop.runs.TaskRunService`) — avant l'audit du 03/10/2026, aucun : la boucle n'était appelée
que par des tests. Chaque donnée renvoyée vient de la base : la TaskRun, ses `EvalRun` (code RÉELLEMENT
généré, exécution, verdict) et ses `Experience` — jamais reconstruite ni estimée.

`POST /tasks/reference` crée la tâche de référence existante (`apex.taskgen.solution_tasks`,
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

_REFERENCE_TASKS = ("is_palindrome",)


class ReferenceTaskIn(BaseModel):
    kind: str = Field(default="is_palindrome")


class TaskRunIn(BaseModel):
    objective: str = Field(default="résoudre la tâche", min_length=1, max_length=2000)
    execution_mode: str = Field(default="bounded")
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_candidates: int = Field(default=2, ge=1, le=5)
    max_wall_clock_s: float = Field(default=900.0, gt=0, le=3600)
    generation_timeout_s: float = Field(default=120.0, gt=0, le=900)


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
    return {
        "id": run.id,
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
    from apex.metalearner.experience import ExperienceRepository

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
    }


@router.post("/tasks/reference", response_model=DataEnvelope[dict[str, Any]])
def create_reference_task(body: ReferenceTaskIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory
    from apex.api.routes.evals import _to_task_out
    from apex.policy.model import Subject
    from apex.taskgen.solution_tasks import build_palindrome_task_spec
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService

    if body.kind not in _REFERENCE_TASKS:
        raise HTTPException(status_code=422, detail=f"tâche de référence « {body.kind} » inconnue")
    spec = build_palindrome_task_spec()
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
