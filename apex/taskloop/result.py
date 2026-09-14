"""`TaskResult` — ce que `apex.taskloop.loop.run_task_loop` retourne. Porte
tout ce que §7 de la mission N+27 exige : quelle tâche, quel statut final,
ce qui a été produit, combien de tentatives, quels providers, ce qui a
réellement été exécuté/jugé, pourquoi une récupération a eu lieu, pourquoi
la boucle s'est arrêtée, quel identifiant retrouve la trace complète
(`EventRepository`, filtrée par `trace_id`).

`TaskLoopStatus` est délibérément DISTINCT de `apex.missions.states.
MissionState` (pas de palier REVIEW humain ici — voir `apex.taskloop.
request` pour le raisonnement) et de `apex.recovery.model.RecoveryAction`
(une ACTION par tentative, pas un statut final de boucle)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from apex.recovery import AttemptRecord


class TaskLoopStatus(str, Enum):
    """Vocabulaire FERMÉ des statuts finaux — exactement les quatre états
    terminaux de `apex.taskloop.model.TaskLoopStage` (`COMPLETE`/`BLOCK`)
    plus `CANCELLED` (jamais un cinquième état terminal implicite)."""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    #: Identifiant de CETTE exécution du TaskLoop — distinct de `task_id`
    #: (une même `Task` peut être retentée par des appels ultérieurs
    #: indépendants, chacun avec son propre `trace_id`). Filtrer
    #: `EventRepository`/`context["trace_id"]` par cette valeur retrouve la
    #: trace complète et exclusive de CETTE exécution.
    trace_id: str
    status: TaskLoopStatus
    #: `SandboxResult.stdout` de la dernière tentative RÉUSSIE (`status is
    #: COMPLETED`) — `None` sinon, jamais un résultat partiel présenté comme
    #: final.
    final_output: str | None
    #: Une entrée par tentative RÉELLEMENT effectuée — réutilise
    #: `apex.recovery.model.AttemptRecord` tel quel (N+26), jamais un second
    #: modèle de tentative.
    attempts: tuple[AttemptRecord, ...]
    #: `ModelCandidate.ref.id` de chaque provider RÉELLEMENT essayé, dans
    #: l'ordre — `len(...)` > 1 seulement si `RecoveryAction.REASSIGN` a
    #: réellement eu lieu.
    providers_tried: tuple[str, ...]
    #: `EvalRun.id` de chaque tentative qui a atteint le jugement (JUDGE) —
    #: absent pour une tentative qui a échoué avant (génération/sandbox).
    eval_run_ids: tuple[str, ...]
    #: Résumé de l'échec final — `None` si `status is COMPLETED`.
    failure: str | None
    #: Raison structurée de l'arrêt (`apex.taskloop.model.StopReason.value`,
    #: ou `"recovery_block"`/`"recovery_escalated"`/`"no_progress"` pour un
    #: arrêt décidé par `apex.recovery.decide`/`detect_no_progress` plutôt
    #: que par `check_stop`) — jamais vide.
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "status": self.status.value,
            "final_output": self.final_output,
            "attempts": [a.to_dict() for a in self.attempts],
            "providers_tried": list(self.providers_tried),
            "eval_run_ids": list(self.eval_run_ids),
            "failure": self.failure,
            "stop_reason": self.stop_reason,
        }
