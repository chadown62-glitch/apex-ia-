"""Persistance des templates d'agent — même séparation qu'`apex.missions.
repository` vis-à-vis d'`apex.missions.model`. Imports vers `apex.db`
différés : `apex.agent_templates` doit rester importable sans l'extra `db`.
"""

from __future__ import annotations

from typing import Any

from apex.agent_templates.model import AgentTemplate


class AgentTemplateRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, template: AgentTemplate) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTemplateRepository as _DbAgentTemplateRepository

        with session_scope(self._session_factory) as session:
            _DbAgentTemplateRepository(session).create(
                id=template.id, name=template.name, role=template.role.value,
                model_role=template.model_role, tools=template.tools, description=template.description,
                status=template.status.value, created_by=template.created_by, created_at=template.created_at,
            )

    def get(self, template_id: str) -> AgentTemplate | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTemplateRepository as _DbAgentTemplateRepository

        with session_scope(self._session_factory) as session:
            row = _DbAgentTemplateRepository(session).get(template_id)
            return AgentTemplate.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[AgentTemplate]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTemplateRepository as _DbAgentTemplateRepository

        with session_scope(self._session_factory) as session:
            rows = _DbAgentTemplateRepository(session).list_all(limit=limit)
            return [AgentTemplate.from_row(row) for row in rows]

    def delete(self, template_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentTemplateRepository as _DbAgentTemplateRepository

        with session_scope(self._session_factory) as session:
            return _DbAgentTemplateRepository(session).delete(template_id)
