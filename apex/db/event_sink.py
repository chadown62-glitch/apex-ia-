"""Implémentation DB de `EventSink` (registre : stub -> implemented, phase 2).

Vient EN PLUS du sink JSON existant (`apex.obs.logging`), pas à sa place —
c'est le choix explicite du brief phase 2. La rédaction de secrets est
réappliquée ici avec la même fonction que le formateur JSON
(`apex.obs.logging.redact`) : un composant qui persiste un événement ne
doit pas pouvoir contourner la garantie de rédaction en écrivant en base
plutôt qu'en journal (§72).

Implémente aussi `HealthCheckable` (principe 13) : `apex doctor` pourra
interroger cette classe comme n'importe quel autre composant.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from apex.db.repositories import EventRepository
from apex.obs.health import ComponentHealth, Status
from apex.obs.logging import redact


class DatabaseEventSink:
    """Persiste les événements en base. Ne lève jamais vers l'appelant.

    Un sink d'observabilité qui plante l'appelant serait pire que l'absence
    d'observabilité — même principe que `apex.doctor.runner.run_checks`.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def emit(
        self,
        *,
        level: str,
        logger: str,
        msg: str,
        context: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> bool:
        """Écrit un événement. Retourne False (jamais d'exception) en cas d'échec."""
        safe_context = redact(context or {})
        session = self._session_factory()
        try:
            EventRepository(session).add(
                ts=ts or datetime.now(timezone.utc),
                level=level,
                logger=logger,
                msg=msg,
                context=safe_context,
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    def health(self, *, timeout_s: float = 2.0) -> ComponentHealth:
        started = time.perf_counter()
        session = self._session_factory()
        try:
            session.execute(text("SELECT 1"))
            latency_ms = (time.perf_counter() - started) * 1000
            if latency_ms > timeout_s * 1000:
                return ComponentHealth(
                    component="db.event_sink",
                    status=Status.DEGRADED,
                    detail=f"connexion lente ({latency_ms:.0f} ms)",
                    latency_ms=latency_ms,
                )
            return ComponentHealth(
                component="db.event_sink", status=Status.OK, latency_ms=latency_ms
            )
        except Exception as exc:
            return ComponentHealth(
                component="db.event_sink",
                status=Status.FAILED,
                detail=f"{exc.__class__.__name__}: {exc}",
            )
        finally:
            session.close()
