"""Persistance des `GauntletResult` — couche domaine au-dessus d'`apex.db`.
Traduit entre `GauntletResult` (`apex.metalearner.judge_model`) et
`GauntletResultRow` (`apex.db.models`) — même séparation qu'entre
`apex.metalearner.learning_session_service` et `apex.db.repositories.
LearningSessionRepository`. Tous les imports vers `apex.db` sont différés :
`apex.metalearner` doit rester importable sans l'extra `db` installé.

INSERT seul — aucune méthode `update`/`delete` (même garantie que
`apex.db.repositories.GauntletResultRepository`)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.judge_model import GauntletResult


class GauntletRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, result: GauntletResult) -> GauntletResult:
        from apex.db.engine import session_scope
        from apex.db.repositories import GauntletResultRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            _DbRepo(db_session).create(
                id=result.id, specialty=result.specialty, generation=result.generation,
                member_id=result.member_id, stage=result.stage.value,
                protocol_version=result.protocol_version, decision=result.decision.value,
                overall_score=result.overall_score, overall_confidence=result.overall_confidence,
                veto=result.veto, veto_dimension=result.veto_dimension.value if result.veto_dimension else None,
                disagreement=result.disagreement,
                stopped_after_critical_failure=result.stopped_after_critical_failure,
                judge_results=[jr.to_dict() for jr in result.judge_results],
                compared_champion_id=result.compared_champion_id, created_at=result.created_at,
            )
        return result

    def get(self, gauntlet_result_id: str) -> GauntletResult | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import GauntletResultRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get(gauntlet_result_id)
            return GauntletResult.from_row(row) if row is not None else None

    def list_by_member(self, member_id: str, *, limit: int = 50) -> list[GauntletResult]:
        from apex.db.engine import session_scope
        from apex.db.repositories import GauntletResultRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_member(member_id, limit=limit)
            return [GauntletResult.from_row(row) for row in rows]

    def list_by_specialty(self, specialty: str, *, limit: int = 50) -> list[GauntletResult]:
        from apex.db.engine import session_scope
        from apex.db.repositories import GauntletResultRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_specialty(specialty, limit=limit)
            return [GauntletResult.from_row(row) for row in rows]
