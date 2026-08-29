"""Accès aux données — une classe par agrégat, aucun SQL brut ailleurs dans le Core.

`AuditLogRepository` n'expose délibérément aucune méthode `update`/`delete` :
c'est une première ligne de défense au niveau Python, en plus de la
révocation `UPDATE`/`DELETE`/`TRUNCATE` appliquée par la migration au
niveau du rôle SQL (`docs/VISION_INTEGRATION.md` §3.3). Les deux sont
nécessaires : le rôle SQL est la garantie réelle, l'absence de méthode
évite qu'un appelant du Core soit même tenté d'essayer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.config.schema import ComputePool
from apex.db.models import AuditLogRow, ComputePoolRow, EventRow


class ComputePoolRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, pool: ComputePool) -> ComputePoolRow:
        row = self._session.get(ComputePoolRow, pool.pool_id)
        if row is None:
            row = ComputePoolRow(pool_id=pool.pool_id)
            self._session.add(row)
        row.location = pool.location.value
        row.trust_level = pool.trust_level.value
        row.capabilities = [c.value for c in pool.capabilities]
        row.cpu_cores = pool.cpu_cores
        row.ram_gb = pool.ram_gb
        row.cost_per_hour = pool.cost_per_hour
        self._session.flush()
        return row

    def get(self, pool_id: str) -> ComputePoolRow | None:
        return self._session.get(ComputePoolRow, pool_id)

    def list_all(self) -> list[ComputePoolRow]:
        return list(self._session.scalars(select(ComputePoolRow).order_by(ComputePoolRow.pool_id)))


class EventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, *, ts: datetime, level: str, logger: str, msg: str, context: dict[str, Any]
    ) -> EventRow:
        row = EventRow(ts=ts, level=level, logger=logger, msg=msg, context=context)
        self._session.add(row)
        self._session.flush()
        return row

    def recent(self, limit: int = 100) -> list[EventRow]:
        stmt = select(EventRow).order_by(EventRow.ts.desc()).limit(limit)
        return list(self._session.scalars(stmt))


class AuditLogRepository:
    """INSERT seul — voir la note du module. Aucune méthode update/delete n'existe ici."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self, *, ts: datetime, actor: str, action: str, target: str | None, details: dict[str, Any]
    ) -> AuditLogRow:
        row = AuditLogRow(ts=ts, actor=actor, action=action, target=target, details=details)
        self._session.add(row)
        self._session.flush()
        return row

    def recent(self, limit: int = 100) -> list[AuditLogRow]:
        stmt = select(AuditLogRow).order_by(AuditLogRow.ts.desc()).limit(limit)
        return list(self._session.scalars(stmt))
