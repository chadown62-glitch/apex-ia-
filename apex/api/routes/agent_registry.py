"""GET/POST/PATCH/DELETE /configured-agents, /agent-teams — Agents V1.

Nommée `/configured-agents` (jamais `/agents`, déjà pris par `apex.api.
routes.agents` — les instances de clone OS-level réelles, `AgentRuntime`) :
cette route expose `apex.agent_registry.model.Agent`, une CONFIGURATION
réutilisable, jamais un clone en cours d'exécution. Même limite déjà
documentée pour `/agents`/`/missions` : un jeton est désormais exigé
(passe « API Authentication Hardening », `apex.api.auth`), mais aucun
mapping appelant -> `Subject` précis n'existe encore, donc toute action ici
est exercée comme si l'appelant était `Subject.HUMAN` (sauf `/executions`,
où le service exige `Subject.CORE` en interne, même motif que
`/missions/{id}/launch`).
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import (
    AgentExecutionOut,
    AgentTeamCreateIn,
    AgentTeamOut,
    ConfiguredAgentCreateIn,
    ConfiguredAgentOut,
    ConfiguredAgentUpdateIn,
    DataEnvelope,
    ExecutionTraceOut,
)

router = APIRouter(tags=["configured-agents"])


def _to_agent_out(agent: object, status: object) -> ConfiguredAgentOut:
    return ConfiguredAgentOut(
        id=agent.id,  # type: ignore[attr-defined]
        name=agent.name,  # type: ignore[attr-defined]
        description=agent.description,  # type: ignore[attr-defined]
        role=agent.role.value,  # type: ignore[attr-defined]
        specialty=agent.specialty,  # type: ignore[attr-defined]
        model_role=agent.model_role,  # type: ignore[attr-defined]
        required_capabilities=list(agent.required_capabilities),  # type: ignore[attr-defined]
        tools=[t.value for t in agent.tools],  # type: ignore[attr-defined]
        permissions=[p.value for p in agent.permissions],  # type: ignore[attr-defined]
        instructions=agent.instructions,  # type: ignore[attr-defined]
        enabled=agent.enabled,  # type: ignore[attr-defined]
        status=status.value,  # type: ignore[attr-defined]
        project_id=agent.project_id,  # type: ignore[attr-defined]
        template_id=agent.template_id,  # type: ignore[attr-defined]
        created_by=agent.created_by,  # type: ignore[attr-defined]
        created_at=agent.created_at.isoformat(),  # type: ignore[attr-defined]
        updated_at=agent.updated_at.isoformat(),  # type: ignore[attr-defined]
    )


def _to_execution_out(execution: object) -> AgentExecutionOut:
    return AgentExecutionOut(
        id=execution.id,  # type: ignore[attr-defined]
        agent_id=execution.agent_id,  # type: ignore[attr-defined]
        mission_id=execution.mission_id,  # type: ignore[attr-defined]
        status=execution.status.value,  # type: ignore[attr-defined]
        model_id=execution.model_id,  # type: ignore[attr-defined]
        provider_id=execution.provider_id,  # type: ignore[attr-defined]
        result=execution.result,  # type: ignore[attr-defined]
        error=execution.error,  # type: ignore[attr-defined]
        fallback_reason=execution.fallback_reason.value if execution.fallback_reason else None,  # type: ignore[attr-defined]
        started_at=execution.started_at.isoformat(),  # type: ignore[attr-defined]
        completed_at=execution.completed_at.isoformat() if execution.completed_at else None,  # type: ignore[attr-defined]
    )


def _to_team_out(team: object) -> AgentTeamOut:
    return AgentTeamOut(
        id=team.id,  # type: ignore[attr-defined]
        name=team.name,  # type: ignore[attr-defined]
        description=team.description,  # type: ignore[attr-defined]
        agent_ids=list(team.agent_ids),  # type: ignore[attr-defined]
        created_by=team.created_by,  # type: ignore[attr-defined]
        created_at=team.created_at.isoformat(),  # type: ignore[attr-defined]
    )


@router.get("/configured-agents", response_model=DataEnvelope[list[ConfiguredAgentOut]])
def list_configured_agents() -> DataEnvelope[list[ConfiguredAgentOut]]:
    try:
        from apex.agent_registry.model import derive_agent_status
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        service = AgentRegistryService(get_session_factory())
        agents = service.list_all()
        views = [_to_agent_out(a, derive_agent_status(a, service.latest_execution(a.id))) for a in agents]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get("/configured-agents/{agent_id}", response_model=DataEnvelope[ConfiguredAgentOut])
def get_configured_agent(agent_id: str) -> DataEnvelope[ConfiguredAgentOut]:
    try:
        from apex.agent_registry.model import derive_agent_status
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        service = AgentRegistryService(get_session_factory())
        agent = service.get(agent_id)
        if agent is None:
            return DataEnvelope(status="unknown", data=None, detail=f"agent « {agent_id} » introuvable")
        status = derive_agent_status(agent, service.latest_execution(agent_id))
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_agent_out(agent, status))


@router.post("/configured-agents", response_model=DataEnvelope[ConfiguredAgentOut])
def create_configured_agent(payload: ConfiguredAgentCreateIn) -> DataEnvelope[ConfiguredAgentOut]:
    try:
        from apex.agent_registry.model import derive_agent_status
        from apex.agent_registry.service import AgentRegistryService
        from apex.agent_templates.model import AgentRole
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        service = AgentRegistryService(get_session_factory())

        if payload.template_id is not None:
            agent = service.instantiate_from_template(
                payload.template_id, name=payload.name, created_by=Subject.HUMAN, project_id=payload.project_id,
            )
        else:
            try:
                role = AgentRole(payload.role)
            except ValueError:
                allowed = ", ".join(sorted(r.value for r in AgentRole))
                return DataEnvelope(
                    status="unknown", data=None,
                    detail=f"rôle « {payload.role} » inconnu — valeurs autorisées : {allowed}",
                )
            agent = service.create(
                name=payload.name, role=role, created_by=Subject.HUMAN, description=payload.description,
                specialty=payload.specialty, model_role=payload.model_role,
                required_capabilities=tuple(payload.required_capabilities), tools=tuple(payload.tools),
                permissions=tuple(payload.permissions), instructions=payload.instructions,
                enabled=payload.enabled, project_id=payload.project_id,
            )
        status = derive_agent_status(agent, None)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_agent_out(agent, status))


@router.patch("/configured-agents/{agent_id}", response_model=DataEnvelope[ConfiguredAgentOut])
def update_configured_agent(agent_id: str, payload: ConfiguredAgentUpdateIn) -> DataEnvelope[ConfiguredAgentOut]:
    try:
        from apex.agent_registry.model import Permission, ToolName, derive_agent_status
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        service = AgentRegistryService(get_session_factory())
        fields: dict = {}
        if payload.name is not None:
            fields["name"] = payload.name
        if payload.description is not None:
            fields["description"] = payload.description
        if payload.model_role is not None:
            fields["model_role"] = payload.model_role
        if payload.required_capabilities is not None:
            fields["required_capabilities"] = tuple(payload.required_capabilities)
        if payload.tools is not None:
            fields["tools"] = tuple(ToolName(t) for t in payload.tools)
        if payload.permissions is not None:
            fields["permissions"] = tuple(Permission(p) for p in payload.permissions)
        if payload.instructions is not None:
            fields["instructions"] = payload.instructions
        if payload.enabled is not None:
            fields["enabled"] = payload.enabled

        agent = service.update(agent_id, requested_by=Subject.HUMAN, **fields)
        status = derive_agent_status(agent, service.latest_execution(agent_id))
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_agent_out(agent, status))


@router.delete("/configured-agents/{agent_id}", response_model=DataEnvelope[bool])
def delete_configured_agent(agent_id: str) -> DataEnvelope[bool]:
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        deleted = AgentRegistryService(get_session_factory()).delete(agent_id, requested_by=Subject.HUMAN)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=deleted)


@router.get("/configured-agents/{agent_id}/executions", response_model=DataEnvelope[list[AgentExecutionOut]])
def list_agent_executions(agent_id: str) -> DataEnvelope[list[AgentExecutionOut]]:
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        executions = AgentRegistryService(get_session_factory()).list_executions(agent_id)
        views = [_to_execution_out(e) for e in executions]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get(
    "/configured-agents/{agent_id}/executions/{execution_id}/trace",
    response_model=DataEnvelope[ExecutionTraceOut],
)
def get_execution_trace(agent_id: str, execution_id: str) -> DataEnvelope[ExecutionTraceOut]:
    """Reconstruit APRÈS COUP la décision de routage (candidat sélectionné,
    score, `ScoreBreakdown`, alternatives écartées) et les tentatives de
    fallback (catégorie réelle de chaque échec) d'UNE exécution — passe
    « Routing & Execution Observability Hardening » (N+18). `agent_id`
    n'est pas réutilisé pour filtrer (l'`execution_id` seul identifie déjà
    l'exécution) : conservé dans le chemin pour rester cohérent avec
    `/configured-agents/{agent_id}/executions`, jamais une seconde clé
    primaire."""
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        trace = AgentRegistryService(get_session_factory()).get_execution_trace(execution_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if trace["routing"] is None and trace["fallback"] is None:
        return DataEnvelope(
            status="unknown", data=None,
            detail=f"aucune trace trouvée pour l'exécution « {execution_id} » (hors fenêtre récente, ou inexistante)",
        )
    return DataEnvelope(status="real", data=ExecutionTraceOut(**trace))


@router.get("/agent-teams", response_model=DataEnvelope[list[AgentTeamOut]])
def list_agent_teams() -> DataEnvelope[list[AgentTeamOut]]:
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory

        teams = AgentRegistryService(get_session_factory()).list_teams()
        views = [_to_team_out(t) for t in teams]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.post("/agent-teams", response_model=DataEnvelope[AgentTeamOut])
def create_agent_team(payload: AgentTeamCreateIn) -> DataEnvelope[AgentTeamOut]:
    try:
        from apex.agent_registry.service import AgentRegistryService
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        team = AgentRegistryService(get_session_factory()).create_team(
            name=payload.name, description=payload.description,
            agent_ids=tuple(payload.agent_ids), created_by=Subject.HUMAN,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_team_out(team))
