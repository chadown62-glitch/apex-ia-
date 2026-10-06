"""Modèle de domaine du Scheduler — dataclasses pures.

Aucun état ici n'est persisté séparément de ce qui existe déjà (`missions`,
`agents`) : le Scheduler ne crée aucune nouvelle table (voir le rapport de
l'étape 8). `ExecutionHandle` est délibérément UNIQUEMENT en mémoire — c'est
ce qui rend `apex.scheduler.recovery.RecoveryService` correct (le registre
est nécessairement vide à chaque nouveau démarrage de processus).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from apex.sandbox.model import CancellationToken


@dataclass
class ExecutionHandle:
    """Poignée en mémoire d'UNE exécution suivie par CETTE instance de
    `SchedulerService`. Ne survit jamais à un redémarrage du processus —
    c'est une garantie structurelle, pas une hypothèse."""

    mission_id: str
    clone_id: str
    cancel_token: CancellationToken = field(default_factory=CancellationToken)
    started_at: datetime | None = None
    #: Mission à Task vérifiable : la TaskRun que le TaskLoop exécute pour elle (`clone_id` vide — le TaskLoop
    #: crée lui-même un clone par tentative). Renseigné dès que la TaskRun a démarré.
    task_run_id: str | None = None


@dataclass(frozen=True)
class SchedulerTickResult:
    """Ce qu'UN tick a décidé — jamais ce qu'il a fini d'exécuter (les
    missions soumises tournent en tâche de fond, voir `SchedulerService.tick`)."""

    submitted: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    skipped_no_capacity: tuple[str, ...] = ()
    skipped_no_agent: tuple[str, ...] = ()
    #: Missions à Task vérifiable laissées QUEUED faute de TaskRunService injecté dans ce Scheduler.
    skipped_no_task_runner: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryReport:
    """Trace explicite d'une réconciliation au démarrage — voir
    `apex.scheduler.recovery.RecoveryService`. Toujours produit, même si les
    deux tuples sont vides : l'absence d'orphelin est aussi un fait à tracer."""

    orphaned_mission_ids: tuple[str, ...]
    orphaned_clone_ids: tuple[str, ...]
    ran_at: datetime
