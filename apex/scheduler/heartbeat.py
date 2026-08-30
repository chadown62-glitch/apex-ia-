"""HeartbeatService — preuve de vivacité RÉELLE, jamais supposée.

Le battement de cœur n'est PAS un indicateur applicatif ("le code a atteint
telle étape") : c'est un constat noyau direct —
`CancellationToken.is_alive()` (lui-même `os.killpg(pgid, 0)`, voir
`apex.sandbox.linux_namespace`) — écrit dans `agents.last_heartbeat_at`
SEULEMENT quand ce constat est positif. Indépendant de l'état supposé de la
mission ou du clone en base : ce module ne lit ni `missions.state` ni
`agents.state`, il interroge directement le processus réel. Aucun battement
n'est jamais écrit « par optimisme » — l'absence de battement récent est
elle-même le signal utile, pas une anomalie à masquer.

Tourne sur EXACTEMENT un thread, quel que soit le nombre d'exécutions
suivies par `ExecutionRegistry` — aucune croissance de threads proportionnelle
au nombre de missions concurrentes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from apex.scheduler.registry import ExecutionRegistry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HeartbeatService:
    def __init__(
        self, session_factory: Any, *, registry: ExecutionRegistry, interval_s: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="apex-scheduler-heartbeat", daemon=True)
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def tick_once(self) -> int:
        """Une itération, appelable directement (tests, ou un futur appel
        manuel) sans passer par le thread de fond. Retourne le nombre de
        battements réellement écrits — jamais un compte optimiste."""
        written = 0
        for handle in self._registry.snapshot():
            if handle.cancel_token.is_alive():
                if self._write_heartbeat(handle.clone_id):
                    written += 1
        return written

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick_once()
            self._stop.wait(self._interval_s)

    def _write_heartbeat(self, clone_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            return AgentRepository(session).touch_heartbeat(clone_id, _utcnow())
