"""Persistance des missions — couche domaine au-dessus d'`apex.db`.

Traduit entre `Mission` (domaine, `apex.missions.model`) et `MissionRow`
(`apex.db.models`), pour qu'`apex.missions.service` ne manipule jamais
directement SQLAlchemy — même séparation qu'entre `apex.agents.runtime` et
`apex.db.repositories.AgentRepository`. Tous les imports vers `apex.db` sont
différés dans le corps des méthodes : `apex.missions` doit rester importable
sans l'extra `db` installé (même garde que `apex.agents`, voir
`tests/test_missions_service.py::TestImportableWithoutSqlalchemy`).
"""

from __future__ import annotations

from typing import Any

from apex.missions.model import Mission


class MissionRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, mission: Mission) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            _DbMissionRepository(session).create(
                id=mission.id, title=mission.title, description=mission.description,
                specialty=mission.specialty, objective=mission.objective,
                priority=mission.priority.value, constraints=mission.constraints,
                assigned_agent=mission.assigned_agent, agent_id=mission.agent_id, state=mission.state.value,
                created_by=mission.created_by, created_at=mission.created_at,
                depends_on=mission.depends_on,
            )

    def update(self, mission: Mission, **overrides: Any) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            _DbMissionRepository(session).update(
                mission.id,
                state=overrides.get("state", mission.state.value),
                assigned_agent=overrides.get("assigned_agent"),
                result=overrides.get("result"),
                error=overrides.get("error"),
                validation_status=overrides.get("validation_status"),
                started_at=overrides.get("started_at"),
                completed_at=overrides.get("completed_at"),
                clear_validation_status=overrides.get("clear_validation_status", False),
            )

    def get(self, mission_id: str) -> Mission | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            row = _DbMissionRepository(session).get(mission_id)
            return Mission.from_row(row) if row is not None else None

    def list_all(self, limit: int = 100) -> list[Mission]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMissionRepository(session).list_all(limit=limit)
            return [Mission.from_row(row) for row in rows]

    def list_by_state(self, state: str, limit: int = 500) -> list[Mission]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMissionRepository(session).list_by_state(state, limit=limit)
            return [Mission.from_row(row) for row in rows]

    def count_by_state(self, state: str) -> int:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            return _DbMissionRepository(session).count_by_state(state)

    def dependency_graph(self) -> dict[str, tuple[str, ...]]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            return _DbMissionRepository(session).dependency_graph()

    def claim_running(self, mission_id: str, *, started_at: Any) -> bool:
        """Réclamation atomique QUEUED -> RUNNING — voir
        `apex.db.repositories.MissionRepository.claim_running`, la seule
        implémentation réelle. Ce wrapper ne fait que respecter la même
        séparation de couche que le reste de ce fichier."""
        from apex.db.engine import session_scope
        from apex.db.repositories import MissionRepository as _DbMissionRepository

        with session_scope(self._session_factory) as session:
            return _DbMissionRepository(session).claim_running(mission_id, started_at=started_at)
