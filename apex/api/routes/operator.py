"""POST/GET /operator — Operator (N+16), couche de contrôle centrale d'APEX.

Ne réimplémente ni le Task System ni l'orchestrateur : chaque route délègue
entièrement à `apex.operator.service.OperatorService` (singleton applicatif,
`apex.api.dependencies.get_operator_service`, construit AU-DESSUS du
`SchedulerService` déjà existant — jamais un second). Même limite déjà
documentée pour `/missions`/`/agents` : un jeton API est exigé mais aucun
mapping appelant -> `Subject` précis n'existe encore, donc tout appelant agit
ici comme `Subject.HUMAN` — pas un contournement de PolicyEngine, la même
hypothèse simplificatrice déjà en place ailleurs, jamais une nouvelle.

Un « Task » au sens Operator EST une `apex.missions.model.Mission` — les
routes `/operator/tasks*` renvoient `MissionOut`, exactement comme
`/missions`, jamais une seconde représentation."""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import (
    DataEnvelope,
    EventOut,
    MissionCreateIn,
    MissionOut,
    OperatorStatusOut,
    TaskResultOut,
)

router = APIRouter(prefix="/operator", tags=["operator"])


def _to_status_out(status: object) -> OperatorStatusOut:
    return OperatorStatusOut(
        state=status.state.value,  # type: ignore[attr-defined]
        since=status.since.isoformat(),  # type: ignore[attr-defined]
        active_task_ids=list(status.active_task_ids),  # type: ignore[attr-defined]
        queued_task_count=status.queued_task_count,  # type: ignore[attr-defined]
        max_concurrent_tasks=status.max_concurrent_tasks,  # type: ignore[attr-defined]
        last_error=status.last_error,  # type: ignore[attr-defined]
    )


def _to_task_result_out(result: object) -> TaskResultOut:
    return TaskResultOut(
        task_id=result.task_id,  # type: ignore[attr-defined]
        state=result.state,  # type: ignore[attr-defined]
        ready=result.ready,  # type: ignore[attr-defined]
        result=result.result,  # type: ignore[attr-defined]
        error=result.error,  # type: ignore[attr-defined]
    )


@router.post("/start", response_model=DataEnvelope[OperatorStatusOut])
def start_operator() -> DataEnvelope[OperatorStatusOut]:
    try:
        from apex.api.dependencies import get_operator_service

        status = get_operator_service().start()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.post("/stop", response_model=DataEnvelope[OperatorStatusOut])
def stop_operator() -> DataEnvelope[OperatorStatusOut]:
    try:
        from apex.api.dependencies import get_operator_service

        status = get_operator_service().stop()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.post("/pause", response_model=DataEnvelope[OperatorStatusOut])
def pause_operator() -> DataEnvelope[OperatorStatusOut]:
    try:
        from apex.api.dependencies import get_operator_service

        status = get_operator_service().pause()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.post("/resume", response_model=DataEnvelope[OperatorStatusOut])
def resume_operator() -> DataEnvelope[OperatorStatusOut]:
    try:
        from apex.api.dependencies import get_operator_service

        status = get_operator_service().resume()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.get("/status", response_model=DataEnvelope[OperatorStatusOut])
def get_operator_status() -> DataEnvelope[OperatorStatusOut]:
    try:
        from apex.api.dependencies import get_operator_service

        status = get_operator_service().status()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.post("/tasks", response_model=DataEnvelope[MissionOut])
def submit_task(payload: MissionCreateIn) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_operator_service
        from apex.api.routes.missions import _to_mission_out
        from apex.missions.model import MissionPriority

        mission = get_operator_service().submit_task(
            title=payload.title, description=payload.description, specialty=payload.specialty,
            objective=payload.objective, priority=MissionPriority(payload.priority),
            constraints=payload.constraints, assigned_agent=payload.assigned_agent, agent_id=payload.agent_id,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("/tasks/{task_id}/cancel", response_model=DataEnvelope[MissionOut])
def cancel_task(task_id: str) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_operator_service
        from apex.api.routes.missions import _to_mission_out

        mission = get_operator_service().cancel_task(task_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.get("/tasks/{task_id}", response_model=DataEnvelope[MissionOut])
def get_task_status(task_id: str) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_operator_service
        from apex.api.routes.missions import _to_mission_out

        mission = get_operator_service().get_task_status(task_id)
        if mission is None:
            return DataEnvelope(status="unknown", data=None, detail=f"tâche « {task_id} » introuvable")
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.get("/tasks/{task_id}/result", response_model=DataEnvelope[TaskResultOut])
def get_task_result(task_id: str) -> DataEnvelope[TaskResultOut]:
    try:
        from apex.api.dependencies import get_operator_service

        result = get_operator_service().get_task_result(task_id)
        if result is None:
            return DataEnvelope(status="unknown", data=None, detail=f"tâche « {task_id} » introuvable")
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_task_result_out(result))


@router.get("/operations", response_model=DataEnvelope[list[EventOut]])
def get_recent_operations(limit: int = 100) -> DataEnvelope[list[EventOut]]:
    try:
        from apex.api.dependencies import get_operator_service

        limit = max(1, min(limit, 500))
        rows = get_operator_service().get_recent_operations(limit=limit)
        data = [
            EventOut(id=r.id, ts=r.ts.isoformat(), level=r.level, logger=r.logger, msg=r.msg, context=r.context)
            for r in rows
        ]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)
