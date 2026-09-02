"""Persistance des `Agent`/`AgentExecution`/`AgentTeam` — couche domaine au-
dessus d'`apex.db`, même séparation qu'`apex.missions.repository`. Imports
vers `apex.db` différés : `apex.agent_registry` doit rester importable sans
l'extra `db` installé.
"""

from __future__ import annotations

from typing import Any

from apex.agent_registry.model import Agent, AgentExecution, AgentExecutionStatus, AgentTeam


class ConfiguredAgentRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, agent: Agent) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository as _DbAgentRepository

        with session_scope(self._session_factory) as session:
            _DbAgentRepository(session).create(
                id=agent.id, name=agent.name, description=agent.description, role=agent.role.value,
                specialty=agent.specialty, model_role=agent.model_role,
                required_capabilities=agent.required_capabilities,
                tools=tuple(t.value for t in agent.tools),
                permissions=tuple(p.value for p in agent.permissions),
                instructions=agent.instructions, enabled=agent.enabled,
                project_id=agent.project_id, template_id=agent.template_id,
                created_by=agent.created_by, created_at=agent.created_at,
            )

    def update(self, agent_id: str, **fields: Any) -> Agent | None:
        from apex.agent_registry.model import _utcnow
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository as _DbAgentRepository

        tools = fields.get("tools")
        permissions = fields.get("permissions")
        with session_scope(self._session_factory) as session:
            row = _DbAgentRepository(session).update(
                agent_id,
                name=fields.get("name"),
                description=fields.get("description"),
                model_role=fields.get("model_role"),
                required_capabilities=fields.get("required_capabilities"),
                tools=tuple(t.value for t in tools) if tools is not None else None,
                permissions=tuple(p.value for p in permissions) if permissions is not None else None,
                instructions=fields.get("instructions"),
                enabled=fields.get("enabled"),
                updated_at=_utcnow(),
            )
            return Agent.from_row(row) if row is not None else None

    def get(self, agent_id: str) -> Agent | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository as _DbAgentRepository

        with session_scope(self._session_factory) as session:
            row = _DbAgentRepository(session).get(agent_id)
            return Agent.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[Agent]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository as _DbAgentRepository

        with session_scope(self._session_factory) as session:
            rows = _DbAgentRepository(session).list_all(limit=limit)
            return [Agent.from_row(row) for row in rows]

    def delete(self, agent_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository as _DbAgentRepository

        with session_scope(self._session_factory) as session:
            return _DbAgentRepository(session).delete(agent_id)


class AgentExecutionRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, *, agent_id: str, mission_id: str) -> AgentExecution:
        import uuid

        from apex.agent_registry.model import _utcnow
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            row = _DbExecutionRepository(session).create(
                id=str(uuid.uuid4()), agent_id=agent_id, mission_id=mission_id,
                status=AgentExecutionStatus.RUNNING.value, started_at=_utcnow(),
            )
            return AgentExecution.from_row(row)

    def complete(
        self,
        execution_id: str,
        *,
        status: AgentExecutionStatus,
        model_id: str | None,
        provider_id: str | None,
        result: str | None,
        error: str | None,
        fallback_reason: str | None,
        elapsed_s: float | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
    ) -> AgentExecution | None:
        from apex.agent_registry.model import _utcnow
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            row = _DbExecutionRepository(session).complete(
                execution_id, status=status.value, model_id=model_id, provider_id=provider_id,
                result=result, error=error, fallback_reason=fallback_reason, completed_at=_utcnow(),
                elapsed_s=elapsed_s, tokens_prompt=tokens_prompt, tokens_completion=tokens_completion,
            )
            return AgentExecution.from_row(row) if row is not None else None

    def list_by_agent(self, agent_id: str, limit: int = 200) -> list[AgentExecution]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExecutionRepository(session).list_by_agent(agent_id, limit=limit)
            return [AgentExecution.from_row(row) for row in rows]

    def list_by_mission(self, mission_id: str, limit: int = 200) -> list[AgentExecution]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExecutionRepository(session).list_by_mission(mission_id, limit=limit)
            return [AgentExecution.from_row(row) for row in rows]

    def latest_for_agent(self, agent_id: str) -> AgentExecution | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            row = _DbExecutionRepository(session).latest_for_agent(agent_id)
            return AgentExecution.from_row(row) if row is not None else None

    def list_by_model(self, model_id: str, limit: int = 500) -> list[AgentExecution]:
        """Passe « Adaptive Model Router V1 » — alimente
        `apex.agent_registry.performance.aggregate_model_performance`."""
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentExecutionRepository as _DbExecutionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExecutionRepository(session).list_by_model(model_id, limit=limit)
            return [AgentExecution.from_row(row) for row in rows]


class AgentTeamRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, team: AgentTeam) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTeamRepository as _DbTeamRepository

        with session_scope(self._session_factory) as session:
            _DbTeamRepository(session).create(
                id=team.id, name=team.name, description=team.description,
                agent_ids=team.agent_ids, created_by=team.created_by, created_at=team.created_at,
            )

    def get(self, team_id: str) -> AgentTeam | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTeamRepository as _DbTeamRepository

        with session_scope(self._session_factory) as session:
            row = _DbTeamRepository(session).get(team_id)
            return AgentTeam.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[AgentTeam]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTeamRepository as _DbTeamRepository

        with session_scope(self._session_factory) as session:
            rows = _DbTeamRepository(session).list_all(limit=limit)
            return [AgentTeam.from_row(row) for row in rows]
