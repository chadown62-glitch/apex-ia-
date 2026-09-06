"""Persistance des membres de population — couche domaine au-dessus
d'`apex.db`. Traduit entre `PopulationMember` (`apex.metalearner.
population_model`) et `PopulationMemberRow` (`apex.db.models`) — même
séparation qu'entre `apex.genome.service` et `apex.db.repositories.
GenomeVersionRepository`. Tous les imports vers `apex.db` sont différés :
`apex.metalearner` doit rester importable sans l'extra `db` installé.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apex.metalearner.population_model import MemberStatus, PopulationMember


class PopulationMemberRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, member: PopulationMember) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            _DbRepo(session).create(
                id=member.id, specialty=member.specialty, generation=member.generation,
                clone_id=member.clone_id, genome_version_id=member.genome_version_id,
                parent_member_id=member.parent_member_id, status=member.status.value,
                created_by=member.created_by, experiment_ref=member.experiment_ref,
                created_at=member.created_at,
            )

    def get(self, member_id: str) -> PopulationMember | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            row = _DbRepo(session).get(member_id)
            return PopulationMember.from_row(row) if row is not None else None

    def get_by_experiment_ref(self, experiment_ref: str) -> PopulationMember | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            row = _DbRepo(session).get_by_experiment_ref(experiment_ref)
            return PopulationMember.from_row(row) if row is not None else None

    def list_by_specialty(self, specialty: str, *, limit: int = 500) -> list[PopulationMember]:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            rows = _DbRepo(session).list_by_specialty(specialty, limit=limit)
            return [PopulationMember.from_row(row) for row in rows]

    def count_active(self, specialty: str) -> int:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            return _DbRepo(session).count_active(specialty)

    def record_fitness(
        self, member_id: str, *, fitness: float, fitness_components: dict[str, Any],
        skill_candidate_id: str | None, genome_candidate_id: str | None,
    ) -> PopulationMember | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            row = _DbRepo(session).record_fitness(
                member_id, fitness=fitness, fitness_components=fitness_components,
                skill_candidate_id=skill_candidate_id, genome_candidate_id=genome_candidate_id,
            )
            return PopulationMember.from_row(row) if row is not None else None

    def set_status(self, member_id: str, *, status: MemberStatus, decided_at: datetime) -> PopulationMember | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import PopulationMemberRepository as _DbRepo

        with session_scope(self._session_factory) as session:
            row = _DbRepo(session).set_status(member_id, status=status.value, decided_at=decided_at)
            return PopulationMember.from_row(row) if row is not None else None
