"""Machine à états d'Operator — même patron que `apex.missions.states`
(état fermé + table de transitions explicite, refus par défaut de toute
transition non listée).

Deux catégories de transitions, toutes deux vérifiées par
`assert_transition_allowed` — il n'existe pas de raccourci qui contournerait
la table pour l'une ou l'autre :

- **Commandées** (Start/Stop/Pause/Resume) — déclenchées explicitement par
  un appelant HUMAIN via `OperatorService`.
- **Observées** (READY <-> RUNNING) — dérivées automatiquement par le
  processus de supervision d'`OperatorService` selon que des tâches sont
  actuellement suivies par le Scheduler (`SchedulerService.registry`) ou
  non ; jamais une décision de l'appelant, uniquement un FAIT constaté.
"""

from __future__ import annotations

from enum import Enum


class OperatorState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAULTED = "faulted"

    @property
    def accepts_submissions(self) -> bool:
        """Vrai si APEX accepte qu'une nouvelle tâche soit soumise
        (READY/RUNNING/PAUSED) — PAUSED accepte toujours la soumission (la
        tâche reste QUEUED) mais ne la fait pas progresser tant que la
        supervision est en pause, voir `OperatorService.pause`."""
        return self in (OperatorState.READY, OperatorState.RUNNING, OperatorState.PAUSED)

    @property
    def is_actively_scheduling(self) -> bool:
        """Vrai si le thread de supervision appelle activement `tick()`
        (READY/RUNNING) — jamais en PAUSED, jamais hors de ces deux états."""
        return self in (OperatorState.READY, OperatorState.RUNNING)


#: Transitions légales. READY <-> RUNNING sont bidirectionnelles et
#: symétriques (constat automatique, jamais une décision) — un appel à
#: `assert_transition_allowed` pour l'une ou l'autre direction est toujours
#: légal depuis l'état READY/RUNNING respectif.
_TRANSITIONS: dict[OperatorState, frozenset[OperatorState]] = {
    OperatorState.STOPPED: frozenset({OperatorState.STARTING}),
    OperatorState.STARTING: frozenset({OperatorState.READY, OperatorState.FAULTED}),
    OperatorState.READY: frozenset({OperatorState.RUNNING, OperatorState.PAUSED, OperatorState.STOPPING}),
    OperatorState.RUNNING: frozenset({OperatorState.READY, OperatorState.PAUSED, OperatorState.STOPPING}),
    OperatorState.PAUSED: frozenset({OperatorState.READY, OperatorState.STOPPING}),
    OperatorState.STOPPING: frozenset({OperatorState.STOPPED, OperatorState.FAULTED}),
    OperatorState.FAULTED: frozenset({OperatorState.STARTING, OperatorState.STOPPED}),
}


class InvalidOperatorTransition(ValueError):
    """Transition demandée absente de la machine à états — jamais rattrapée
    silencieusement (même discipline qu'`apex.missions.states.
    InvalidMissionTransition`)."""


def assert_transition_allowed(current: OperatorState, target: OperatorState) -> None:
    allowed = _TRANSITIONS[current]
    if target not in allowed:
        raise InvalidOperatorTransition(
            f"transition « {current.value} » -> « {target.value} » invalide "
            f"(autorisées : {sorted(s.value for s in allowed) or 'aucune'})"
        )
