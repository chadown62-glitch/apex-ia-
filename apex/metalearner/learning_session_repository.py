"""Persistance des sessions d'apprentissage — couche domaine au-dessus
d'`apex.db`. Traduit entre `LearningSession` (`apex.metalearner.
learning_session_model`) et `LearningSessionRow` (`apex.db.models`) — même
séparation qu'entre `apex.metalearner.population_service` et
`apex.db.repositories.PopulationMemberRepository`. Tous les imports vers
`apex.db` sont différés : `apex.metalearner` doit rester importable sans
l'extra `db` installé.

**Seul point de traduction IntegrityError -> `LearningSessionAlreadyRunning`**
— `create`/`transition_to_running` peuvent heurter l'index UNIQUE partiel
(`learning_sessions_one_running_per_specialty_idx`, migration) si une AUTRE
session de la même spécialité est déjà `running` ; cette couche est la
SEULE à savoir traduire cette violation SQL générique en une exception
métier précise, jamais laissée remonter telle quelle à l'appelant."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apex.errors import LearningSessionAlreadyRunning
from apex.metalearner.learning_session_model import LearningSession, LearningSessionStatus


class LearningSessionRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, session: LearningSession) -> LearningSession:
        from sqlalchemy.exc import IntegrityError

        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        try:
            with session_scope(self._session_factory) as db_session:
                _DbRepo(db_session).create(
                    id=session.id, specialty=session.specialty, status=session.status.value,
                    max_generations=session.max_generations, convergence_patience=session.convergence_patience,
                    created_by=session.created_by, created_at=session.created_at,
                )
        except IntegrityError as exc:
            raise LearningSessionAlreadyRunning(session.specialty) from exc
        return session

    def get(self, session_id: str) -> LearningSession | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get(session_id)
            return LearningSession.from_row(row) if row is not None else None

    def list_by_specialty(self, specialty: str, *, limit: int = 50) -> list[LearningSession]:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_specialty(specialty, limit=limit)
            return [LearningSession.from_row(row) for row in rows]

    def list_all(self, *, limit: int = 50) -> list[LearningSession]:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_all(limit=limit)
            return [LearningSession.from_row(row) for row in rows]

    def transition_to_running(
        self, session_id: str, *, from_statuses: tuple[LearningSessionStatus, ...], specialty: str,
    ) -> LearningSession | None:
        from sqlalchemy.exc import IntegrityError

        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        try:
            with session_scope(self._session_factory) as db_session:
                row = _DbRepo(db_session).transition_to_running(
                    session_id, from_statuses=tuple(s.value for s in from_statuses),
                )
                return LearningSession.from_row(row) if row is not None else None
        except IntegrityError as exc:
            raise LearningSessionAlreadyRunning(specialty) from exc

    def record_generation(
        self,
        session_id: str,
        *,
        generations_completed: int,
        generations_since_improvement: int,
        best_member_id: str | None,
        best_fitness: float | None,
        best_genome_version_id: str | None,
    ) -> LearningSession | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).record_generation(
                session_id, generations_completed=generations_completed,
                generations_since_improvement=generations_since_improvement,
                best_member_id=best_member_id, best_fitness=best_fitness,
                best_genome_version_id=best_genome_version_id,
            )
            return LearningSession.from_row(row) if row is not None else None

    def finish(
        self, session_id: str, *, status: LearningSessionStatus, stop_reason: str, error: str | None,
        completed_at: datetime,
    ) -> LearningSession | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).finish(
                session_id, status=status.value, stop_reason=stop_reason, error=error, completed_at=completed_at,
            )
            return LearningSession.from_row(row) if row is not None else None

    def request_cancel(self, session_id: str) -> LearningSession | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import LearningSessionRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).request_cancel(session_id)
            return LearningSession.from_row(row) if row is not None else None
