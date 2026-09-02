"""AgentTemplateService — création/consultation/suppression de DÉFINITIONS
d'agent. Ne spawn jamais de clone réel : `SPAWN_CLONE`/`AgentRuntime` restent
entièrement hors du périmètre de ce service, volontairement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.agent_templates.model import AgentRole, AgentTemplate
from apex.agent_templates.repository import AgentTemplateRepository
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentTemplateService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = AgentTemplateRepository(session_factory)

    def create(
        self,
        *,
        name: str,
        role: AgentRole,
        description: str,
        created_by: Subject,
        model_role: str | None = None,
        tools: tuple[str, ...] = (),
    ) -> AgentTemplate:
        self.policy.authorize(created_by, Action.CREATE_AGENT_TEMPLATE)
        if not name.strip():
            raise ValueError("le nom du template est obligatoire")
        template = AgentTemplate.new(
            name=name, role=role, description=description, created_by=created_by.value,
            model_role=model_role, tools=tools,
        )
        self._repo.create(template)
        self._record_event(template, "agent_template.created", {})
        self._record_audit(template, actor=created_by.value, action="agent_template.created", details={})
        return template

    def get(self, template_id: str) -> AgentTemplate | None:
        return self._repo.get(template_id)

    def list_all(self, limit: int = 200) -> list[AgentTemplate]:
        return self._repo.list_all(limit=limit)

    def delete(self, template_id: str, *, requested_by: Subject) -> bool:
        self.policy.authorize(requested_by, Action.DELETE_AGENT_TEMPLATE)
        template = self._repo.get(template_id)
        if template is None:
            return False
        deleted = self._repo.delete(template_id)
        if deleted:
            self._record_audit(
                template, actor=requested_by.value, action="agent_template.deleted", details={},
            )
        return deleted

    def _record_event(self, template: AgentTemplate, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.agent_templates",
                msg=kind, context={"template_id": template.id, "kind": kind, **details},
            )

    def _record_audit(self, template: AgentTemplate, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=template.id, details=details,
            )
