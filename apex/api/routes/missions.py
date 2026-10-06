"""GET/POST /missions — Mission Engine (étape 6).

Même limite déjà documentée dans `apex/api/routes/agents.py` : un jeton est
désormais exigé (passe « API Authentication Hardening », `apex.api.auth`),
mais aucun mapping appelant -> `Subject` précis n'existe encore, donc toute
action ici est exercée comme si l'appelant était `Subject.HUMAN` — pas un
contournement de PolicyEngine, la même hypothèse simplificatrice déjà en
place pour `/agents`, pas une nouvelle.

`mark_running`/`mark_review`/`mark_failed` (`apex.missions.service`) ne sont
toujours PAS exposées ici individuellement.

**Changement de comportement — étape 11.** `/launch` n'appelle plus
`apex.missions.execution.launch_mission` directement : c'est désormais une
DEMANDE D'ORDONNANCEMENT adressée au Scheduler applicatif
(`apex.api.dependencies.get_scheduler_service`, singleton paresseux — voir sa
docstring). `launch_mission` reste la primitive interne que le Scheduler
seul appelle (`apex.scheduler.service.SchedulerService._launch_and_track`) ;
elle n'est plus exposée comme point d'orchestration HTTP normal.

Signature et forme de réponse (`DataEnvelope[MissionOut]`) inchangées.
Comportement observable inchangé sur le chemin heureux actuel (agent READY,
mission QUEUED, capacité disponible) : la route reste bloquante et renvoie
l'état REVIEW/FAILED atteint, comme avant — via `SchedulerService.
wait_for_mission`, borné, plutôt qu'un appel synchrone direct. Trois
différences réelles, assumées et documentées, pas une régression :

1. Les dépendances non satisfaites (`Mission.depends_on`) sont désormais
   vérifiées à `/launch` — elles ne l'étaient PAS avant (le Scheduler les
   vérifiait déjà, mais rien ne l'appelait). Une mission bloquée renvoie
   `status="unknown"`, reste QUEUED, jamais perdue.
2. Un appel à `/launch` déclenche un `tick()` complet : d'AUTRES missions
   QUEUED éligibles peuvent être lancées par le même appel, effet de bord du
   Scheduler traitant sa queue entière — pas limité à `mission_id`.
3. Sous capacité insuffisante (`max_concurrent_missions`), la réponse est
   immédiate (`status="unknown"`, détail explicite) plutôt qu'une exécution
   forcée : la mission reste QUEUED, un appel ultérieur la reprend.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import (
    AgentExecutionOut,
    DataEnvelope,
    MissionCreateIn,
    MissionOut,
    MissionRejectIn,
    RunAgentExecutionIn,
)

router = APIRouter(prefix="/missions", tags=["missions"])


def _to_mission_out(mission: object) -> MissionOut:
    return MissionOut(
        id=mission.id,  # type: ignore[attr-defined]
        title=mission.title,  # type: ignore[attr-defined]
        description=mission.description,  # type: ignore[attr-defined]
        specialty=mission.specialty,  # type: ignore[attr-defined]
        objective=mission.objective,  # type: ignore[attr-defined]
        priority=mission.priority.value,  # type: ignore[attr-defined]
        constraints=mission.constraints,  # type: ignore[attr-defined]
        assigned_agent=mission.assigned_agent,  # type: ignore[attr-defined]
        agent_id=mission.agent_id,  # type: ignore[attr-defined]
        state=mission.state.value,  # type: ignore[attr-defined]
        result=mission.result,  # type: ignore[attr-defined]
        error=mission.error,  # type: ignore[attr-defined]
        validation_status=mission.validation_status.value if mission.validation_status else None,  # type: ignore[attr-defined]
        created_by=mission.created_by,  # type: ignore[attr-defined]
        created_at=mission.created_at.isoformat(),  # type: ignore[attr-defined]
        started_at=mission.started_at.isoformat() if mission.started_at else None,  # type: ignore[attr-defined]
        completed_at=mission.completed_at.isoformat() if mission.completed_at else None,  # type: ignore[attr-defined]
        task_id=getattr(mission, "task_id", None),
        task_options=getattr(mission, "task_options", None),
        task_run_id=getattr(mission, "task_run_id", None),
    )


@router.get("", response_model=DataEnvelope[list[MissionOut]])
def list_missions() -> DataEnvelope[list[MissionOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService

        missions = MissionService(get_session_factory()).list_all()
        views = [_to_mission_out(m) for m in missions]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get("/{mission_id}", response_model=DataEnvelope[MissionOut])
def get_mission(mission_id: str) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService

        mission = MissionService(get_session_factory()).get(mission_id)
        if mission is None:
            return DataEnvelope(status="unknown", data=None, detail=f"mission « {mission_id} » introuvable")
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("", response_model=DataEnvelope[MissionOut])
def create_mission(payload: MissionCreateIn) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.model import MissionPriority
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        mission = MissionService(get_session_factory()).create(
            title=payload.title, description=payload.description, specialty=payload.specialty,
            objective=payload.objective, created_by=Subject.HUMAN,
            priority=MissionPriority(payload.priority), constraints=payload.constraints,
            assigned_agent=payload.assigned_agent, agent_id=payload.agent_id,
            task_id=payload.task_id, task_options=payload.task_options,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("/{mission_id}/launch", response_model=DataEnvelope[MissionOut])
def launch_mission_route(mission_id: str) -> DataEnvelope[MissionOut]:
    """Demande d'ordonnancement adressée au Scheduler — étape 11. Voir le
    docstring du module pour le changement de comportement précis par
    rapport à l'étape 7.

    QUEUED -> (Scheduler.tick() : réclamation atomique) -> RUNNING -> SANDBOX
    réel -> REVIEW/FAILED, via `apex.missions.execution.launch_mission`
    (`requested_by=Subject.CORE` — jamais HUMAN, inchangé depuis l'étape 7 ;
    PolicyEngine n'accorde LAUNCH_MISSION/EXECUTE_IN_SANDBOX qu'à CORE,
    vérification entièrement déléguée, jamais dupliquée ici ni dans le
    Scheduler). Seule la spécialité « research » a une charge de travail
    réelle (apex.missions.workloads) : toute autre mission échoue proprement.

    La lecture de l'état courant, avant tout `tick()`, est un constat rapide
    et NON AUTORITAIRE — la seule garantie qui compte (aucune mission lancée
    deux fois) reste l'UPDATE atomique de `MissionService.mark_running`,
    inchangé depuis l'étape 8.
    """
    try:
        from apex.api.dependencies import get_scheduler_service, get_session_factory
        from apex.missions.service import MissionService
        from apex.missions.states import MissionState

        session_factory = get_session_factory()
        mission_service = MissionService(session_factory)
        mission = mission_service.get(mission_id)
        if mission is None:
            return DataEnvelope(status="unknown", data=None, detail=f"mission « {mission_id} » introuvable")
        if mission.state is not MissionState.QUEUED:
            return DataEnvelope(
                status="unknown", data=None,
                detail=(
                    f"mission « {mission_id} » n'est pas QUEUED "
                    f"(état actuel : {mission.state.value}) — rien à ordonnancer"
                ),
            )

        scheduler = get_scheduler_service()
        tick_result = scheduler.tick()

        if mission_id in scheduler.registry or mission_id in tick_result.submitted:
            scheduler.wait_for_mission(mission_id, timeout_s=30.0)
        elif mission_id in tick_result.blocked:
            return DataEnvelope(
                status="unknown", data=_to_mission_out(mission_service.get(mission_id) or mission),
                detail="mission bloquée par une ou plusieurs dépendances non satisfaites",
            )
        elif mission_id in tick_result.skipped_no_agent:
            return DataEnvelope(
                status="unknown", data=None,
                detail="mission sans agent assigné — impossible de l'ordonnancer",
            )
        elif mission_id in tick_result.skipped_no_capacity:
            return DataEnvelope(
                status="unknown", data=_to_mission_out(mission_service.get(mission_id) or mission),
                detail="capacité du Scheduler insuffisante pour le moment — la mission reste QUEUED",
            )
        # Sinon : course perdue entre la lecture ci-dessus et ce tick (rare,
        # p.ex. un autre worker/instance a réclamé la mission entre-temps) —
        # on rapporte ci-dessous l'état réel constaté, jamais une supposition.

        current = mission_service.get(mission_id)
        if current is None:
            return DataEnvelope(status="unknown", data=None, detail=f"mission « {mission_id} » introuvable")
        return DataEnvelope(status="real", data=_to_mission_out(current))
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")


@router.post("/{mission_id}/cancel", response_model=DataEnvelope[MissionOut])
def cancel_mission(mission_id: str) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        mission = MissionService(get_session_factory()).cancel(mission_id, requested_by=Subject.HUMAN)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("/{mission_id}/validate", response_model=DataEnvelope[MissionOut])
def validate_mission(mission_id: str) -> DataEnvelope[MissionOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        mission = MissionService(get_session_factory()).validate(mission_id, requested_by=Subject.HUMAN)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("/{mission_id}/reject", response_model=DataEnvelope[MissionOut])
def reject_mission(mission_id: str, payload: MissionRejectIn | None = None) -> DataEnvelope[MissionOut]:
    reason = payload.reason if payload is not None else None
    try:
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        mission = MissionService(get_session_factory()).reject(mission_id, requested_by=Subject.HUMAN, reason=reason)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_mission_out(mission))


@router.post("/{mission_id}/run", response_model=DataEnvelope[AgentExecutionOut])
def run_mission_via_agent(mission_id: str, payload: RunAgentExecutionIn | None = None) -> DataEnvelope[AgentExecutionOut]:
    """TROISIÈME point d'entrée d'exécution de Mission (passe « Agents V1 »)
    — DISTINCT de `/launch` (sandbox réelle, `apex.missions.execution.
    launch_mission`, spécialité « research » seule) : `/run` pilote
    `AgentRegistryService.run_execution` pour la `Mission.agent_id`
    (l'entité `Agent`, jamais `assigned_agent`/un clone). Réutilise
    `apex.routing.select_model`/`generate_with_fallback` et le cycle de vie
    RÉEL de `MissionService` — jamais un second Model Router ni un second
    moteur de Mission."""
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        session_factory = get_session_factory()
        mission_service = MissionService(session_factory)
        mission = mission_service.get(mission_id)
        if mission is None:
            return DataEnvelope(status="unknown", data=None, detail=f"mission « {mission_id} » introuvable")
        if mission.agent_id is None:
            return DataEnvelope(
                status="unknown", data=None,
                detail=f"mission « {mission_id} » n'a aucun agent_id assigné — rien à exécuter via un Agent",
            )
        prompt = payload.prompt if payload is not None else None
        execution = AgentRegistryService(session_factory).run_execution(
            mission.agent_id, mission_id, mission_service=mission_service,
            prompt=prompt, requested_by=Subject.CORE,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    from apex.api.routes.agent_registry import _to_execution_out

    return DataEnvelope(status="real", data=_to_execution_out(execution))


@router.get("/{mission_id}/executions", response_model=DataEnvelope[list[AgentExecutionOut]])
def list_mission_executions(mission_id: str) -> DataEnvelope[list[AgentExecutionOut]]:
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        executions = AgentRegistryService(get_session_factory()).list_executions_for_mission(mission_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    from apex.api.routes.agent_registry import _to_execution_out

    return DataEnvelope(status="real", data=[_to_execution_out(e) for e in executions])
