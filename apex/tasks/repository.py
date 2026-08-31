"""Persistance des tâches — couche domaine au-dessus d'`apex.db`.

Traduit entre `Task` (domaine, `apex.tasks.model`) et `TaskRow`
(`apex.db.models`), pour qu'`apex.tasks.service` ne manipule jamais
directement SQLAlchemy — même séparation qu'entre `apex.missions.service` et
`apex.db.repositories.MissionRepository`. Tous les imports vers `apex.db`
sont différés dans le corps des méthodes : `apex.tasks` doit rester
importable sans l'extra `db` installé (même garde que `apex.missions`).

**INSERT seul** — aucune méthode `update`/`delete` ici, à l'image
d'`apex.missions.repository` n'exposant que `create`/`get`/lecture pour les
agrégats immuables. Une tâche ne change jamais après coup.
"""

from __future__ import annotations

from typing import Any

from apex.tasks.model import Task


class TaskRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, task: Task) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import TaskRepository as _DbTaskRepository

        with session_scope(self._session_factory) as session:
            _DbTaskRepository(session).create(
                id=task.id, split=task.split.value, specialty=task.specialty,
                title=task.title, description=task.description, command=task.command,
                dataset_ref=task.dataset_ref, created_by=task.created_by,
                created_at=task.created_at,
            )

    def get(self, task_id: str) -> Task | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import TaskRepository as _DbTaskRepository

        with session_scope(self._session_factory) as session:
            row = _DbTaskRepository(session).get(task_id)
            return Task.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[Task]:
        from apex.db.engine import session_scope
        from apex.db.repositories import TaskRepository as _DbTaskRepository

        with session_scope(self._session_factory) as session:
            rows = _DbTaskRepository(session).list_all(limit=limit)
            return [Task.from_row(row) for row in rows]

    def list_by_split(self, split: str, limit: int = 200) -> list[Task]:
        from apex.db.engine import session_scope
        from apex.db.repositories import TaskRepository as _DbTaskRepository

        with session_scope(self._session_factory) as session:
            rows = _DbTaskRepository(session).list_by_split(split, limit=limit)
            return [Task.from_row(row) for row in rows]

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[Task]:
        from apex.db.engine import session_scope
        from apex.db.repositories import TaskRepository as _DbTaskRepository

        with session_scope(self._session_factory) as session:
            rows = _DbTaskRepository(session).list_by_specialty(specialty, limit=limit)
            return [Task.from_row(row) for row in rows]
