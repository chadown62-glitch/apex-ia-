"""Persistance de MemoryProvider — couche domaine au-dessus d'`apex.db`.

Traduit entre `MemoryRecord`/`MemoryRelation` (domaine, `apex.memory.model`)
et `MemoryRecordRow`/`MemoryRelationRow` (`apex.db.models`), pour
qu'`apex.memory.service` ne manipule jamais directement SQLAlchemy — même
séparation qu'entre `apex.missions.service` et `apex.db.repositories.
MissionRepository`. Tous les imports vers `apex.db` sont différés dans le
corps des méthodes : `apex.memory` doit rester importable sans l'extra `db`
installé (même garde que `apex.missions`, voir
`tests/test_memory_service.py::TestImportableWithoutSqlalchemy`).
"""

from __future__ import annotations

from typing import Any

from apex.memory.model import MemoryRecord, MemoryRelation


class MemoryRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # -- records -----------------------------------------------------------

    def create_record(self, record: MemoryRecord) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            _DbMemoryRepository(session).create_record(
                id=record.id, kind=record.kind.value, domain=record.domain, content=record.content,
                source=record.source, created_by=record.created_by, confidence=record.confidence,
                verification_status=record.verification_status.value, visibility=record.visibility.value,
                evidence_for=record.evidence_for, evidence_against=record.evidence_against,
                external_provenance=record.external_provenance, external_source=record.external_source,
                supersedes_id=record.supersedes_id, expires_at=record.expires_at,
                created_at=record.created_at, category=record.category.value if record.category else None,
            )

    def promote_to_shared(self, record_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            return _DbMemoryRepository(session).promote_to_shared(record_id)

    def get_record(self, record_id: str) -> MemoryRecord | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            row = _DbMemoryRepository(session).get_record(record_id)
            return MemoryRecord.from_row(row) if row is not None else None

    def list_records(self, *, visibility: str | None = None, limit: int = 200) -> list[MemoryRecord]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMemoryRepository(session).list_records(visibility=visibility, limit=limit)
            return [MemoryRecord.from_row(row) for row in rows]

    def list_system_records(self, *, categories: list[str], limit: int) -> list[MemoryRecord]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMemoryRepository(session).list_system_records(categories=categories, limit=limit)
            return [MemoryRecord.from_row(row) for row in rows]

    def has_successor(self, record_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            return _DbMemoryRepository(session).find_successor(record_id) is not None

    # -- relations -----------------------------------------------------------

    def create_relation(self, relation: MemoryRelation) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            _DbMemoryRepository(session).create_relation(
                id=relation.id, source_id=relation.source_id, target_id=relation.target_id,
                relation=relation.relation.value, provenance=relation.provenance,
                created_by=relation.created_by, confidence=relation.confidence,
                created_at=relation.created_at,
            )

    def list_relations_for(self, record_id: str, limit: int = 200) -> list[MemoryRelation]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MemoryRepository as _DbMemoryRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMemoryRepository(session).list_relations_for(record_id, limit=limit)
            return [MemoryRelation.from_row(row) for row in rows]
