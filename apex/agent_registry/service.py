"""AgentRegistryService — cycle de vie d'un `Agent` et exécution réelle
(`run_execution`). Voir la docstring d'`apex.agent_registry.model` pour
pourquoi `run_execution` REUTILISE `apex.routing.select_model`/
`generate_with_fallback`/`apex.missions.service.MissionService.
mark_running/mark_review/mark_failed` plutôt que de dupliquer le cycle de
vie de Mission ou d'inventer un second Model Router.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from apex.agent_registry.fallback import classify_outcome
from apex.agent_registry.model import (
    Agent,
    AgentExecution,
    AgentExecutionStatus,
    AgentTeam,
    FallbackReason,
    Permission,
    ToolName,
)
from apex.agent_registry.repository import AgentExecutionRepository, AgentTeamRepository, ConfiguredAgentRepository
from apex.agent_templates.model import AgentRole
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject

if TYPE_CHECKING:
    from apex.config.schema import ApexConfig
    from apex.missions.model import Mission
    from apex.missions.service import MissionService
    from apex.models.provider import ModelProvider
    from apex.models.registry import ModelCandidate
    from apex.routing.model import RoutingRequest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRegistryService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._agents = ConfiguredAgentRepository(session_factory)
        self._executions = AgentExecutionRepository(session_factory)
        self._teams = AgentTeamRepository(session_factory)

    # -- cycle de vie de l'Agent ---------------------------------------------

    def create(
        self,
        *,
        name: str,
        role: AgentRole,
        created_by: Subject,
        description: str = "",
        specialty: str | None = None,
        model_role: str | None = None,
        required_capabilities: tuple[str, ...] = (),
        tools: tuple[str, ...] = (),
        permissions: tuple[str, ...] = (),
        instructions: str | None = None,
        enabled: bool = True,
        project_id: str | None = None,
        template_id: str | None = None,
    ) -> Agent:
        """Aucune permission/outil accordé implicitement (directive §7/§6) :
        `permissions`/`tools` valent exactement ce que l'appelant a fourni,
        jamais un défaut « tout accordé »."""
        self.policy.authorize(created_by, Action.CREATE_AGENT)
        if not name.strip():
            raise ValueError("le nom de l'agent est obligatoire")

        from apex.routing.model import Capability

        valid_caps = {c.value for c in Capability}
        unknown_caps = set(required_capabilities) - valid_caps
        if unknown_caps:
            raise ValueError(f"capacité(s) inconnue(s) : {sorted(unknown_caps)}")

        agent = Agent.new(
            name=name, role=role, created_by=created_by.value, description=description,
            specialty=specialty, model_role=model_role, required_capabilities=tuple(required_capabilities),
            tools=tuple(ToolName(t) for t in tools), permissions=tuple(Permission(p) for p in permissions),
            instructions=instructions, enabled=enabled, project_id=project_id, template_id=template_id,
        )
        self._agents.create(agent)
        self._record_event(agent.id, "agent.created", {"role": role.value})
        self._record_audit(agent.id, actor=created_by.value, action="agent.created", details={"role": role.value})
        return agent

    def instantiate_from_template(
        self, template_id: str, *, name: str, created_by: Subject, project_id: str | None = None,
    ) -> Agent:
        """§3 de la directive : « un template doit pouvoir être instancié en
        Agent réel ». Copie la configuration déclarée par le template — ne
        réinterprète jamais ses champs."""
        from apex.agent_templates.repository import AgentTemplateRepository

        template = AgentTemplateRepository(self._session_factory).get(template_id)
        if template is None:
            raise LookupError(f"template « {template_id} » introuvable")
        return self.create(
            name=name, role=template.role, created_by=created_by, description=template.description,
            model_role=template.model_role, tools=tuple(), permissions=tuple(),
            instructions=None, project_id=project_id, template_id=template.id,
        )

    def update(self, agent_id: str, *, requested_by: Subject, **fields: Any) -> Agent:
        self.policy.authorize(requested_by, Action.UPDATE_AGENT)
        agent = self._agents.update(agent_id, **fields)
        if agent is None:
            raise LookupError(f"agent « {agent_id} » introuvable")
        self._record_audit(agent.id, actor=requested_by.value, action="agent.updated", details={})
        return agent

    def delete(self, agent_id: str, *, requested_by: Subject) -> bool:
        self.policy.authorize(requested_by, Action.DELETE_AGENT)
        deleted = self._agents.delete(agent_id)
        if deleted:
            self._record_audit(agent_id, actor=requested_by.value, action="agent.deleted", details={})
        return deleted

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def list_all(self, limit: int = 200) -> list[Agent]:
        return self._agents.list_all(limit=limit)

    def list_executions(self, agent_id: str, limit: int = 200) -> list[AgentExecution]:
        return self._executions.list_by_agent(agent_id, limit=limit)

    def list_executions_for_mission(self, mission_id: str, limit: int = 200) -> list[AgentExecution]:
        return self._executions.list_by_mission(mission_id, limit=limit)

    def latest_execution(self, agent_id: str) -> AgentExecution | None:
        return self._executions.latest_for_agent(agent_id)

    # -- Team V1 minimale -----------------------------------------------------

    def create_team(
        self, *, name: str, description: str, agent_ids: tuple[str, ...], created_by: Subject,
    ) -> AgentTeam:
        self.policy.authorize(created_by, Action.CREATE_AGENT_TEAM)
        if not name.strip():
            raise ValueError("le nom de la team est obligatoire")
        for agent_id in agent_ids:
            if self._agents.get(agent_id) is None:
                raise ValueError(f"agent « {agent_id} » introuvable")
        team = AgentTeam.new(name=name, description=description, agent_ids=agent_ids, created_by=created_by.value)
        self._teams.create(team)
        self._record_audit(team.id, actor=created_by.value, action="agent_team.created", details={})
        return team

    def list_teams(self, limit: int = 200) -> list[AgentTeam]:
        return self._teams.list_all(limit=limit)

    # -- exécution réelle -------------------------------------------------------

    def run_execution(
        self,
        agent_id: str,
        mission_id: str,
        *,
        mission_service: "MissionService",
        config: "ApexConfig | None" = None,
        provider_factory: "Callable[[ModelCandidate], ModelProvider] | None" = None,
        prompt: str | None = None,
        timeout_s: float = 60.0,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requested_by: Subject = Subject.CORE,
    ) -> AgentExecution:
        """UI -> API -> `AgentRegistryService.run_execution` -> `apex.
        routing.select_model` -> `apex.routing.generate_with_fallback` ->
        `apex.models.provider.get_default_provider` -> provider réel ->
        réponse réelle. La Mission traverse son cycle de vie RÉEL
        (`MissionService.mark_running`/`mark_review`/`mark_failed`, même
        gouvernance qu'ADR-0011) — cette méthode ne le contourne jamais,
        elle le pilote avec les mêmes primitives que `generate_mission_
        response_via_router`, sans les dupliquer."""
        self.policy.authorize(requested_by, Action.RUN_AGENT_EXECUTION)

        agent = self._agents.get(agent_id)
        if agent is None:
            raise LookupError(f"agent « {agent_id} » introuvable")
        if not agent.enabled:
            raise ValueError(f"agent « {agent_id} » désactivé — non exécutable")
        if not agent.has_permission(Permission.EXECUTE):
            raise ValueError(f"agent « {agent_id} » n'a pas la permission EXECUTE")

        mission = mission_service.get(mission_id)
        if mission is None:
            raise LookupError(f"mission « {mission_id} » introuvable")

        execution = self._executions.create(agent_id=agent_id, mission_id=mission_id)
        self._record_event(
            agent.id, "agent_execution.started", {"execution_id": execution.id, "mission_id": mission_id},
        )

        from apex.models.registry import filter_by_role, resolve_candidates
        from apex.routing.model import Capability, RoutingRequest
        from apex.routing.router import select_model

        resolved_config = config if config is not None else self._load_config()
        candidates: Sequence[ModelCandidate] = resolve_candidates(resolved_config)
        if agent.model_role is not None:
            candidates = filter_by_role(candidates, agent.model_role)

        request = RoutingRequest(
            required_capabilities=frozenset(Capability(c) for c in agent.required_capabilities),
        )
        decision = select_model(list(candidates), request)
        self._record_event(agent.id, "agent_execution.model_selected", {"execution_id": execution.id})

        # QUEUED -> RUNNING une seule fois, avant toute tentative — même
        # discipline que `generate_mission_response_via_router` : la Mission
        # passe par RUNNING même si aucun candidat n'est finalement éligible,
        # pour ne jamais sauter directement DRAFT/QUEUED -> FAILED.
        mission_service.mark_running(mission_id, requested_by=requested_by)

        if decision.selected is None:
            failed_mission = mission_service.mark_failed(mission_id, error=decision.reason)
            return self._complete(
                execution, mission=failed_mission, model_id=None, provider_id=None,
                result=None, error=decision.reason,
                fallback_reason=classify_outcome(decision, None),
            )

        ranked = [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0]
        resolved_prompt = prompt if prompt is not None else mission.objective

        from apex.routing.generation import generate_with_fallback

        fallback_kwargs: dict[str, Any] = dict(
            prompt=resolved_prompt, max_tokens=max_tokens, temperature=temperature, timeout_s=timeout_s,
        )
        if provider_factory is not None:
            fallback_kwargs["provider_factory"] = provider_factory

        fallback_result = generate_with_fallback(ranked, **fallback_kwargs)

        if fallback_result.result is None:
            detail = "; ".join(f"{a.candidate.ref.id} : {a.error}" for a in fallback_result.attempts)
            error = f"tous les candidats ont échoué ({len(fallback_result.attempts)}) — {detail}"
            failed_mission = mission_service.mark_failed(mission_id, error=error)
            return self._complete(
                execution, mission=failed_mission, model_id=None, provider_id=None,
                result=None, error=error, fallback_reason=classify_outcome(decision, fallback_result),
            )

        served = fallback_result.succeeded_with
        assert served is not None
        provider_id = f"{served.endpoint.runtime}:{served.endpoint.url}" if served.endpoint is not None else None
        reviewed_mission = mission_service.mark_review(mission_id, result=fallback_result.result.text)
        return self._complete(
            execution, mission=reviewed_mission, model_id=served.ref.id, provider_id=provider_id,
            result=fallback_result.result.text, error=None,
            fallback_reason=classify_outcome(decision, fallback_result),
        )

    def _complete(
        self,
        execution: AgentExecution,
        *,
        mission: "Mission",
        model_id: str | None,
        provider_id: str | None,
        result: str | None,
        error: str | None,
        fallback_reason: FallbackReason,
    ) -> AgentExecution:
        from apex.missions.states import MissionState

        status = AgentExecutionStatus.COMPLETED if mission.state is MissionState.REVIEW else AgentExecutionStatus.FAILED
        completed = self._executions.complete(
            execution.id, status=status, model_id=model_id, provider_id=provider_id,
            result=result, error=error, fallback_reason=fallback_reason.value,
        )
        assert completed is not None
        self._record_event(
            execution.agent_id,
            "agent_execution.completed" if status is AgentExecutionStatus.COMPLETED else "agent_execution.failed",
            {"execution_id": execution.id, "mission_id": mission.id, "fallback_reason": fallback_reason.value},
        )
        return completed

    @staticmethod
    def _load_config() -> "ApexConfig":
        from apex.config.loader import load_config

        return load_config()

    # -- traçabilité -----------------------------------------------------------

    def _record_event(self, target_id: str, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.agent_registry",
                msg=kind, context={"agent_id": target_id, "kind": kind, **details},
            )

    def _record_audit(self, target_id: str, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=target_id, details=details,
            )
