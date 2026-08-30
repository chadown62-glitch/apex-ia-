"""GET /events — journal `events` (EXISTS, phase 2, partitionné mensuellement).

Import de SQLAlchemy différé À L'INTÉRIEUR de la fonction de route, jamais
au niveau module : ce fichier doit rester importable (donc la route
enregistrable par FastAPI) même sans l'extra `db` installé — seul l'appel
réel dégrade en `status: "unknown"`.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from apex.api.schemas import DataEnvelope, EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=DataEnvelope[list[EventOut]])
def list_events(limit: int = Query(default=100, ge=1, le=500)) -> DataEnvelope[list[EventOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(get_session_factory()) as session:
            rows = EventRepository(session).recent(limit=limit)
    except Exception as exc:  # DB absente, injoignable, ou extra non installé
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")

    data = [
        EventOut(id=r.id, ts=r.ts.isoformat(), level=r.level, logger=r.logger, msg=r.msg, context=r.context)
        for r in rows
    ]
    return DataEnvelope(status="real", data=data)
