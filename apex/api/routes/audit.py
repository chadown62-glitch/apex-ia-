"""GET /audit — `audit_log` (EXISTS, INSERT-only, phase 2). Lecture seule.

Aucune écriture exposée ici : `AuditLogRepository` n'a pas de méthode
update/delete (voir apex/db/repositories.py) et cette API n'expose même pas
d'endpoint d'écriture — écrire dans audit_log depuis l'API exigerait une
authentification de l'appelant HTTP, qui n'existe pas encore (voir le
registre, entrée ControlCenterAPI).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from apex.api.schemas import AuditLogOut, DataEnvelope

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=DataEnvelope[list[AuditLogOut]])
def list_audit_log(limit: int = Query(default=100, ge=1, le=500)) -> DataEnvelope[list[AuditLogOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        with session_scope(get_session_factory()) as session:
            rows = AuditLogRepository(session).recent(limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")

    data = [
        AuditLogOut(id=r.id, ts=r.ts.isoformat(), actor=r.actor, action=r.action, target=r.target, details=r.details)
        for r in rows
    ]
    return DataEnvelope(status="real", data=data)
