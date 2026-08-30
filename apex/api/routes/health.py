"""GET /health — santé du processus API. Ne lève jamais, même sans DB."""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import DataEnvelope, HealthComponentOut, HealthOut
from apex.obs.health import ComponentHealth, Status, aggregate

router = APIRouter(prefix="/health", tags=["health"])


def _check_database() -> ComponentHealth:
    """Import différé (voir apex/api/dependencies.py) — ne lève jamais."""
    try:
        from sqlalchemy import text

        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope

        with session_scope(get_session_factory()) as session:
            session.execute(text("SELECT 1"))
        return ComponentHealth(component="database", status=Status.OK, detail="joignable")
    except Exception as exc:
        return ComponentHealth(
            component="database",
            status=Status.DEGRADED,
            detail=f"{exc.__class__.__name__}: {exc}",
            fallback_active=True,
        )


@router.get("", response_model=DataEnvelope[HealthOut])
def get_health() -> DataEnvelope[HealthOut]:
    components = [
        ComponentHealth(component="api", status=Status.OK, detail="processus démarré"),
        _check_database(),
    ]
    system = aggregate(components)
    out = HealthOut(
        status=system.status.value,
        components=[
            HealthComponentOut(
                component=c.component,
                status=c.status.value,
                detail=c.detail,
                version=c.version,
                fallback_active=c.fallback_active,
                latency_ms=c.latency_ms,
            )
            for c in system.components
        ],
    )
    # L'enveloppe est "real" : on a réellement mesuré, à l'instant, ce que
    # l'on rapporte — même si le sous-composant "database" est DEGRADED.
    return DataEnvelope(status="real", data=out)
