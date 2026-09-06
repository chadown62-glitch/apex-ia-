"""Persistance des `JudgeLearningSignal` — couche domaine au-dessus
d'`apex.db`. Traduit entre `JudgeLearningSignal` (`apex.metalearner.
judge_feedback_model`) et `JudgeLearningSignalRow` (`apex.db.models`) —
même séparation qu'entre `apex.metalearner.gauntlet_repository.
GauntletRepository` et `apex.db.repositories.GauntletResultRepository`.
Tous les imports vers `apex.db` sont différés : `apex.metalearner` doit
rester importable sans l'extra `db` installé.

INSERT seul — aucune méthode `update`/`delete` (§10 de la mission :
l'historique complet est la source de vérité)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.judge_feedback_model import JudgeLearningSignal


class JudgeLearningSignalRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, signal: JudgeLearningSignal) -> JudgeLearningSignal:
        from apex.db.engine import session_scope
        from apex.db.repositories import JudgeLearningSignalRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            _DbRepo(db_session).create(
                id=signal.id, gauntlet_result_id=signal.gauntlet_result_id, member_id=signal.member_id,
                specialty=signal.specialty, generation=signal.generation,
                genome_version_id=signal.genome_version_id, judge_id=signal.judge_id,
                judge_version=signal.judge_version, dimension=signal.dimension.value,
                decision=signal.decision.value, category=signal.category.value,
                failure_category=signal.failure_category.value if signal.failure_category else None,
                severity=signal.severity.value if signal.severity else None, score=signal.score,
                confidence=signal.confidence, finding_summary=signal.finding_summary,
                evidence_reference=signal.evidence_reference, protocol_version=signal.protocol_version,
                created_at=signal.created_at,
            )
        return signal

    def get(self, signal_id: str) -> JudgeLearningSignal | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import JudgeLearningSignalRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get(signal_id)
            return JudgeLearningSignal.from_row(row) if row is not None else None

    def list_by_specialty(
        self, specialty: str, *, dimension: str | None = None, limit: int = 500,
    ) -> list[JudgeLearningSignal]:
        from apex.db.engine import session_scope
        from apex.db.repositories import JudgeLearningSignalRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_specialty(specialty, dimension=dimension, limit=limit)
            return [JudgeLearningSignal.from_row(row) for row in rows]

    def list_by_member(self, member_id: str, *, limit: int = 200) -> list[JudgeLearningSignal]:
        from apex.db.engine import session_scope
        from apex.db.repositories import JudgeLearningSignalRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_member(member_id, limit=limit)
            return [JudgeLearningSignal.from_row(row) for row in rows]
