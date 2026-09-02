"""ProjectService — création et consultation de projets. Structure minimale
volontaire (voir `apex.projects` docstring) : aucune mutation ni suppression
pour cette passe, seulement créer/lister/consulter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.projects.model import Project
from apex.projects.repository import ProjectRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = ProjectRepository(session_factory)

    def create(self, *, name: str, description: str, created_by: Subject) -> Project:
        self.policy.authorize(created_by, Action.CREATE_PROJECT)
        if not name.strip():
            raise ValueError("le nom du projet est obligatoire")
        project = Project.new(name=name, description=description, created_by=created_by.value)
        self._repo.create(project)
        self._record_event(project, "project.created", {})
        self._record_audit(project, actor=created_by.value, action="project.created", details={})
        return project

    def get(self, project_id: str) -> Project | None:
        return self._repo.get(project_id)

    def list_all(self, limit: int = 200) -> list[Project]:
        return self._repo.list_all(limit=limit)

    def _record_event(self, project: Project, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.projects",
                msg=kind, context={"project_id": project.id, "kind": kind, **details},
            )

    def _record_audit(self, project: Project, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=project.id, details=details,
            )
