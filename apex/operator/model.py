"""Modèle de domaine d'Operator — dataclasses pures, aucun état persisté
séparément de ce qui existe déjà (`missions`, `events`, `audit_log`) : même
discipline qu'`apex.scheduler.model` (« le Scheduler ne crée aucune nouvelle
table »). Un « Task » au sens Operator EST une `apex.missions.model.Mission`
— jamais un second concept parallèle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apex.operator.states import OperatorState


@dataclass(frozen=True)
class OperatorStatus:
    """Snapshot observable du lifecycle d'APEX — tout ce qu'une future UI a
    besoin de lire pour afficher l'état réel sans analyser les logs."""

    state: OperatorState
    since: datetime
    active_task_ids: tuple[str, ...]
    queued_task_count: int
    max_concurrent_tasks: int
    last_error: str | None = None


@dataclass(frozen=True)
class TaskResult:
    """Résultat d'une tâche — reflète directement
    `apex.missions.model.Mission.result`/`.error`, jamais une réinterprétation.
    `ready` distingue « pas encore de résultat » (mission non terminale) d'un
    résultat vide légitime (chaîne vide)."""

    task_id: str
    state: str
    ready: bool
    result: str | None
    error: str | None
