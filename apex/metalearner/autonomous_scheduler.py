"""`AutonomousLearningScheduler` — déclenche périodiquement un cycle
d'apprentissage autonome via `apex.metalearner.autonomy.
AutonomousLearningService.start_cycle`, jamais un second moteur
d'apprentissage. Même patron qu'`apex.scheduler.heartbeat.HeartbeatService` :
exactement UN thread de fond, `start()`/`stop()` idempotents,
`tick_once()` appelable directement (tests, ou un futur appel manuel) sans
passer par le thread.

**Désactivé par défaut** (`apex.config.schema.AutonomousLearningPolicy.
enabled = False`) — `start()` ne démarre RÉELLEMENT le thread que si la
politique le permet ; sinon c'est un no-op silencieux mais observable
(`is_running` reste `False`). Installer/mettre à jour APEX n'active donc
jamais l'apprentissage autonome par surprise — voir la docstring de
`AutonomousLearningPolicy`.

Ne démarre jamais un second cycle si un précédent est encore `RUNNING` :
`AutonomousLearningService.start_cycle` lève déjà `CycleAlreadyRunning`
dans ce cas — ce module l'attrape et journalise un tick « ignoré », jamais
une erreur qui remonterait et arrêterait le thread.
"""

from __future__ import annotations

import threading
from typing import Any

from apex.errors import CycleAlreadyRunning
from apex.evalrunner.model import EvalMethod
from apex.metalearner.autonomy import AutonomousLearningService
from apex.obs.logging import get_logger
from apex.tasks.model import TaskSplit

log = get_logger("metalearner.autonomous_scheduler")


class AutonomousLearningScheduler:
    def __init__(
        self,
        service: AutonomousLearningService,
        *,
        config: Any,
    ) -> None:
        self._service = service
        self._policy = config.autonomous_learning
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None

    @property
    def enabled(self) -> bool:
        return bool(self._policy.enabled)

    @property
    def interval_s(self) -> float:
        return float(self._policy.interval_s)

    def start(self) -> None:
        """No-op si `enabled=False` (défaut) ou déjà démarré — jamais un
        second thread, jamais une erreur pour un appel redondant (même
        idempotence que `HeartbeatService.start`)."""
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="apex-autonomous-learning-scheduler", daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Arrête le déclenchement de NOUVEAUX cycles — ne coupe jamais un
        cycle déjà `RUNNING` (celui-ci continue jusqu'à sa fin naturelle,
        suivi par `AutonomousLearningService`, indépendant de ce thread).
        `join(timeout_s)` borné : jamais un arrêt qui bloque indéfiniment."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def tick_once(self) -> bool:
        """Une itération, appelable directement sans passer par le thread de
        fond. Retourne `True` si un cycle a réellement été démarré, `False`
        si un cycle était déjà `RUNNING` (tick ignoré, jamais une erreur)."""
        try:
            self._service.start_cycle(
                specialty=self._policy.specialty, current_level=self._policy.current_level,
                method=EvalMethod(self._policy.method), split=TaskSplit(self._policy.split),
            )
        except CycleAlreadyRunning:
            log.info("cycle autonome déjà en cours — tick ignoré")
            return False
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:  # le Scheduler ne doit jamais planter le processus
                log.exception("échec inattendu du déclenchement autonome — cycle suivant inchangé")
            self._stop.wait(self._policy.interval_s)
