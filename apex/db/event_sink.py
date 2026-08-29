"""Implémentation DB de `EventSink` (registre : stub -> implemented, phase 2).

Vient EN PLUS du sink JSON existant (`apex.obs.logging`), pas à sa place —
c'est le choix explicite du brief phase 2. La rédaction de secrets est
réappliquée ici avec la même fonction que le formateur JSON
(`apex.obs.logging.redact`) : un composant qui persiste un événement ne
doit pas pouvoir contourner la garantie de rédaction en écrivant en base
plutôt qu'en journal (§72).

Implémente aussi `HealthCheckable` (principe 13) : `apex doctor` pourra
interroger cette classe comme n'importe quel autre composant.

Import de SQLAlchemy volontairement différé à l'intérieur des méthodes, pas
au niveau du module — même raison que `apex.config.loader.load_config` pour
PyYAML (ADR-0002) : `apex.interfaces.registry.module_importable()` doit
pouvoir importer ce module et constater que `DatabaseEventSink` existe même
sur un environnement où l'extra `db` n'est pas installé. Le registre décrit
ce qui EXISTE dans le code, pas ce qui est installé dans le venv courant —
sinon `apex doctor` casserait sur "interfaces.integrity" simplement parce
que `pip install -e ".[db]"` n'a pas été lancé, ce qui n'a rien à voir avec
une régression du code.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from apex.obs.health import ComponentHealth, Status
from apex.obs.logging import redact

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


class DatabaseEventSink:
    """Persiste les événements en base. Ne lève jamais vers l'appelant.

    Un sink d'observabilité qui plante l'appelant serait pire que l'absence
    d'observabilité — même principe que `apex.doctor.runner.run_checks`.
    L'extra `db` manquant se traduit par un échec propre (False / DEGRADED),
    jamais par une exception qui remonte à l'appelant.
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
        try:
            from apex.db.repositories import EventRepository

            session = self._session_factory()
        except Exception:
            return False
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
        try:
            from sqlalchemy import text

            session = self._session_factory()
        except Exception as exc:
            return ComponentHealth(
                component="db.event_sink",
                status=Status.FAILED,
                detail=f"{exc.__class__.__name__}: {exc}",
            )
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
