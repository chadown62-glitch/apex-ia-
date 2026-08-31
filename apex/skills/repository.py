"""Persistance des compétences — couche domaine au-dessus d'`apex.db`.

Traduit entre `Skill` (domaine, `apex.skills.model`) et `SkillRow`
(`apex.db.models`), pour qu'`apex.skills.service` ne manipule jamais
directement SQLAlchemy — même séparation qu'entre `apex.missions.service` et
`apex.db.repositories.MissionRepository`. Tous les imports vers `apex.db`
sont différés dans le corps des méthodes : `apex.skills` doit rester
importable sans l'extra `db` installé.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apex.skills.model import Skill, SkillStatus


class SkillRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, skill: Skill) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            _DbSkillRepository(session).create(
                id=skill.id, name=skill.name, specialty=skill.specialty,
                description=skill.description, status=skill.status.value,
                prerequisite_skill_ids=skill.prerequisite_skill_ids,
                evidence_run_ids=skill.evidence_run_ids, confidence=skill.confidence,
                supersedes_id=skill.supersedes_id, created_by=skill.created_by,
                created_at=skill.created_at,
            )

    def decide(
        self, skill_id: str, *, status: SkillStatus, decided_by: str,
        decided_at: datetime, decision_reason: str | None,
    ) -> Skill | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            row = _DbSkillRepository(session).decide(
                skill_id, status=status.value, decided_by=decided_by,
                decided_at=decided_at, decision_reason=decision_reason,
            )
            return Skill.from_row(row) if row is not None else None

    def get(self, skill_id: str) -> Skill | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            row = _DbSkillRepository(session).get(skill_id)
            return Skill.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[Skill]:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            rows = _DbSkillRepository(session).list_all(limit=limit)
            return [Skill.from_row(row) for row in rows]

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[Skill]:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            rows = _DbSkillRepository(session).list_by_specialty(specialty, limit=limit)
            return [Skill.from_row(row) for row in rows]

    def find_successor(self, skill_id: str) -> Skill | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            row = _DbSkillRepository(session).find_successor(skill_id)
            return Skill.from_row(row) if row is not None else None

    def dependency_graph(self) -> dict[str, tuple[str, ...]]:
        from apex.db.engine import session_scope
        from apex.db.repositories import SkillRepository as _DbSkillRepository

        with session_scope(self._session_factory) as session:
            return _DbSkillRepository(session).dependency_graph()
