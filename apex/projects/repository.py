"""Persistance des projets — couche domaine au-dessus d'`apex.db`, même
séparation qu'`apex.missions.repository` vis-à-vis d'`apex.missions.model`.
Tous les imports vers `apex.db` sont différés : `apex.projects` doit rester
importable sans l'extra `db` installé.
"""

from __future__ import annotations

from typing import Any

from apex.projects.model import Project


class ProjectRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, project: Project) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectRepository as _DbProjectRepository

        with session_scope(self._session_factory) as session:
            _DbProjectRepository(session).create(
                id=project.id, name=project.name, description=project.description,
                created_by=project.created_by, created_at=project.created_at,
                instructions=project.instructions or None,
            )

    def update(self, project_id: str, **fields: str | None) -> Project | None:
        from datetime import UTC, datetime

        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectRepository as _DbProjectRepository

        with session_scope(self._session_factory) as session:
            row = _DbProjectRepository(session).update(project_id, updated_at=datetime.now(UTC), **fields)
            return Project.from_row(row) if row is not None else None

    def delete(self, project_id: str) -> dict[str, int] | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectRepository as _DbProjectRepository

        with session_scope(self._session_factory) as session:
            return _DbProjectRepository(session).delete(project_id)

    def get(self, project_id: str) -> Project | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectRepository as _DbProjectRepository

        with session_scope(self._session_factory) as session:
            row = _DbProjectRepository(session).get(project_id)
            return Project.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[Project]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ProjectRepository as _DbProjectRepository

        with session_scope(self._session_factory) as session:
            rows = _DbProjectRepository(session).list_all(limit=limit)
            return [Project.from_row(row) for row in rows]
