"""Persistance des métadonnées de version — couche domaine au-dessus
d'`apex.db`. Traduit entre `GenomeVersion` (domaine, `apex.genome.model`) et
`GenomeVersionRow` (`apex.db.models`) — même séparation qu'entre
`apex.skills.service` et `apex.db.repositories.SkillRepository`. Tous les
imports vers `apex.db` sont différés : `apex.genome` doit rester importable
sans l'extra `db` installé.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apex.genome.model import GenomeVersion, GenomeVersionStatus


class GenomeVersionRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, version: GenomeVersion) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import GenomeVersionRepository as _DbGenomeVersionRepository

        with session_scope(self._session_factory) as session:
            _DbGenomeVersionRepository(session).create(
                id=version.id, content_hash=version.content_hash,
                parent_version_id=version.parent_version_id,
                parent_version_id_b=version.parent_version_id_b, origin=version.origin.value,
                status=version.status.value, created_by=version.created_by, created_at=version.created_at,
            )

    def decide(
        self, version_id: str, *, status: GenomeVersionStatus, decided_by: str,
        decided_at: datetime, decision_reason: str | None,
    ) -> GenomeVersion | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import GenomeVersionRepository as _DbGenomeVersionRepository

        with session_scope(self._session_factory) as session:
            row = _DbGenomeVersionRepository(session).decide(
                version_id, status=status.value, decided_by=decided_by,
                decided_at=decided_at, decision_reason=decision_reason,
            )
            return GenomeVersion.from_row(row) if row is not None else None

    def get(self, version_id: str) -> GenomeVersion | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import GenomeVersionRepository as _DbGenomeVersionRepository

        with session_scope(self._session_factory) as session:
            row = _DbGenomeVersionRepository(session).get(version_id)
            return GenomeVersion.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[GenomeVersion]:
        from apex.db.engine import session_scope
        from apex.db.repositories import GenomeVersionRepository as _DbGenomeVersionRepository

        with session_scope(self._session_factory) as session:
            rows = _DbGenomeVersionRepository(session).list_all(limit=limit)
            return [GenomeVersion.from_row(row) for row in rows]

    def get_current(self) -> GenomeVersion | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import GenomeVersionRepository as _DbGenomeVersionRepository

        with session_scope(self._session_factory) as session:
            row = _DbGenomeVersionRepository(session).get_current()
            return GenomeVersion.from_row(row) if row is not None else None
