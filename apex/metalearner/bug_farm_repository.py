"""Persistance des `BugRecord` — couche domaine au-dessus d'`apex.db`.
Traduit entre `BugRecord` (`apex.metalearner.bug_farm_model`) et
`BugRecordRow` (`apex.db.models`) — même séparation qu'entre
`apex.metalearner.challenge_repository.CurriculumChallengeRepository` et
`apex.db.repositories.CurriculumChallengeRepository`. Tous les imports vers
`apex.db` sont différés : `apex.metalearner` doit rester importable sans
l'extra `db` installé.

`update_status` est la SEULE méthode de mutation (voir
`apex.db.repositories.BugRecordRepository.update_status`)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.bug_farm_model import BugRecord


class BugRecordRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, bug: BugRecord) -> BugRecord:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            _DbRepo(db_session).create(
                id=bug.id, specialty=bug.specialty, weakness_id=bug.weakness_id,
                dimension=bug.dimension.value,
                failure_category=bug.failure_category, severity=bug.severity.value,
                first_seen_generation=bug.first_seen_generation, first_seen_at=bug.first_seen_at,
                member_id=bug.member_id, genome_version_id=bug.genome_version_id,
                evidence_reference=bug.evidence_reference, status=bug.status.value,
            )
        return bug

    def get(self, bug_id: str) -> BugRecord | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get(bug_id)
            return BugRecord.from_row(row) if row is not None else None

    def get_by_weakness(self, weakness_id: str) -> BugRecord | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).get_by_weakness(weakness_id)
            return BugRecord.from_row(row) if row is not None else None

    def list_by_specialty(self, specialty: str, *, limit: int = 200) -> list[BugRecord]:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            rows = _DbRepo(db_session).list_by_specialty(specialty, limit=limit)
            return [BugRecord.from_row(row) for row in rows]

    def update_status(
        self, bug_id: str, *, status: str, fixed_at_generation: int | None = None,
        regressed_at_generation: int | None = None,
    ) -> BugRecord | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).update_status(
                bug_id, status=status, fixed_at_generation=fixed_at_generation,
                regressed_at_generation=regressed_at_generation,
            )
            return BugRecord.from_row(row) if row is not None else None

    def set_evidence_reference(self, bug_id: str, *, evidence_reference: str) -> BugRecord | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import BugRecordRepository as _DbRepo

        with session_scope(self._session_factory) as db_session:
            row = _DbRepo(db_session).set_evidence_reference(bug_id, evidence_reference=evidence_reference)
            return BugRecord.from_row(row) if row is not None else None
