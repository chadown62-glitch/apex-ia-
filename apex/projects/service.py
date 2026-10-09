"""ProjectService — créer, consulter, renommer/modifier (nom, description, consignes) et supprimer un projet
(0.4.0). Supprimer un projet supprime ce qui lui appartient — conversations, messages, tours, pièces jointes et
espace de travail — jamais une conversation laissée orpheline ; l'audit garde ce qui a été supprimé (comptes).
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

    def create(self, *, name: str, description: str, created_by: Subject, instructions: str = "") -> Project:
        self.policy.authorize(created_by, Action.CREATE_PROJECT)
        if not name.strip():
            raise ValueError("le nom du projet est obligatoire")
        project = Project.new(name=name.strip(), description=description, created_by=created_by.value,
                              instructions=instructions)
        self._repo.create(project)
        self._record_event(project, "project.created", {})
        self._record_audit(project, actor=created_by.value, action="project.created", details={})
        return project

    def get(self, project_id: str) -> Project | None:
        return self._repo.get(project_id)

    def update(self, project_id: str, *, requested_by: Subject, **fields: str | None) -> Project:
        self.policy.authorize(requested_by, Action.UPDATE_PROJECT)
        unknown = set(fields) - {"name", "description", "instructions"}
        if unknown:
            raise ValueError(f"champ(s) non modifiable(s) : {', '.join(sorted(unknown))}")
        if "name" in fields:
            if not (fields["name"] or "").strip():
                raise ValueError("le nom du projet est obligatoire")
            fields["name"] = (fields["name"] or "").strip()
        project = self._repo.update(project_id, **fields)
        if project is None:
            raise LookupError(f"projet « {project_id} » introuvable")
        self._record_audit(project, actor=requested_by.value, action="project.updated",
                           details={"fields": sorted(fields)})
        return project

    def delete(self, project_id: str, *, requested_by: Subject) -> dict[str, int]:
        self.policy.authorize(requested_by, Action.DELETE_PROJECT)
        project = self._repo.get(project_id)
        if project is None:
            raise LookupError(f"projet « {project_id} » introuvable")
        counts = self._repo.delete(project_id) or {}
        self._record_audit(project, actor=requested_by.value, action="project.deleted", details=counts)
        from apex.assistant.workspace import remove

        try:
            counts["workspace_removed"] = int(remove("project", project_id))
        except (OSError, ValueError):
            counts["workspace_removed"] = 0
        return counts

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
