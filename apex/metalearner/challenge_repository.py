"""Persistance des `CurriculumChallenge` — couche domaine au-dessus
d'`apex.db`. Traduit entre `CurriculumChallenge` (`apex.metalearner.
challenge_model`) et `CurriculumChallengeRow` (`apex.db.models`) — même
séparation qu'entre `apex.metalearner.judge_feedback_repository.
JudgeLearningSignalRepository` et `apex.db.repositories.
JudgeLearningSignalRepository`. Tous les imports vers `apex.db` sont
différés : `apex.metalearner` doit rester importable sans l'extra `db`
installé.

INSERT seul — un challenge produit n'est jamais modifié ni supprimé (même
garantie que `JudgeLearningSignalRepository`)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.challenge_model import CurriculumChallenge


class CurriculumChallengeRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, challenge: CurriculumChallenge) -> CurriculumChallenge:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            _DbRepo(db_session).create(
                id=challenge.id, specialty=challenge.specialty, weakness_id=challenge.weakness_id,
                dimension=challenge.dimension.value, difficulty=challenge.difficulty,
                generation=challenge.generation, task_id=challenge.task_id,
                evaluation_criteria=challenge.evaluation_criteria, source=challenge.source.value,
                heldout=challenge.heldout, adversarial=challenge.adversarial,
                protocol_version=challenge.protocol_version, created_at=challenge.created_at,
                parent_task_id=challenge.parent_task_id, seed=challenge.seed,
            )
        return challenge

    def get(self, challenge_id: str) -> CurriculumChallenge | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get(challenge_id)
            return CurriculumChallenge.from_row(row) if row is not None else None

    def get_by_task_id(self, task_id: str) -> CurriculumChallenge | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get_by_task_id(task_id)
            return CurriculumChallenge.from_row(row) if row is not None else None

    def list_by_parent_task_id(self, parent_task_id: str, *, limit: int = 200) -> list[CurriculumChallenge]:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_parent_task_id(parent_task_id, limit=limit)
            return [CurriculumChallenge.from_row(row) for row in rows]

    def list_by_specialty(self, specialty: str, *, limit: int = 200) -> list[CurriculumChallenge]:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_specialty(specialty, limit=limit)
            return [CurriculumChallenge.from_row(row) for row in rows]

    def list_by_weakness(self, weakness_id: str, *, limit: int = 200) -> list[CurriculumChallenge]:
        from apex.db.engine import session_scope
        from apex.db.repositories import CurriculumChallengeRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_weakness(weakness_id, limit=limit)
            return [CurriculumChallenge.from_row(row) for row in rows]
