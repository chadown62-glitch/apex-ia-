"""Modèle de domaine d'une Task — dataclass pure, aucune dépendance DB
(même séparation que `apex.missions.model.Mission` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import différé — même raison que Mission.from_row : apex.tasks doit
    # rester importable sans l'extra `db` installé.
    from apex.db.models import TaskRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskSplit(str, Enum):
    """Critère HELDOUT (ADR-0010). `TRAIN` : lisible par tout exécuteur
    autorisé. `HELDOUT` : la garantie de séparation n'est PAS portée par
    cette valeur elle-même (une colonne ne protège rien) mais par l'absence
    totale de `GRANT` sur `tasks` pour le rôle `apex_eval` (migration
    `eda413ca0829`) — un exécuteur ne peut structurellement pas lire une
    ligne `tasks`, HELDOUT ou non, qu'il connaisse ou non son `id`."""

    TRAIN = "train"
    HELDOUT = "heldout"


@dataclass
class Task:
    """Une tâche d'évaluation réutilisable — l'énoncé, pas la tentative.

    Volontairement **pas de champ `method`** : voir `apex.db.models.TaskRow`
    pour le raisonnement (`method` est une propriété du *run*, pas de la
    tâche — `docs/COGNITIVE_ARCHITECTURE.md` §5). `command` est un **argv**
    (tuple de chaînes), jamais une chaîne shell — même contrat que
    `apex.sandbox.model.SandboxSpec.command`, qui la reçoit directement telle
    quelle (`apex.evalrunner.service`) — jamais une tentative générée par un
    `ModelProvider` pour CE chemin déterministe (`EvalRunnerService.run`,
    inchangé).

    `expected_answer` (passe « Real Model Evaluation ») : réponse attendue en
    texte libre, optionnelle, utilisée UNIQUEMENT par
    `apex.oracles.response_judge.ExactMatchResponseJudge` pour juger une
    tentative générée par un `ModelProvider` (`EvalRunnerService.
    run_model_eval`, chemin distinct de `run`) — jamais consultée par
    `ExitCodeOracle`/`ExactMatchResponseJudge`. `None` par défaut : une tâche
    déterministe existante n'a jamais besoin de ce champ, jamais rétrofitté.
    """

    id: str
    split: TaskSplit
    specialty: str
    title: str
    description: str
    command: tuple[str, ...]
    dataset_ref: str | None
    created_by: str
    expected_answer: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        split: TaskSplit,
        specialty: str,
        title: str,
        description: str,
        command: tuple[str, ...],
        created_by: str,
        dataset_ref: str | None = None,
        expected_answer: str | None = None,
    ) -> Task:
        return cls(
            id=str(uuid.uuid4()),
            split=split,
            specialty=specialty,
            title=title,
            description=description,
            command=tuple(command),
            dataset_ref=dataset_ref,
            created_by=created_by,
            expected_answer=expected_answer,
        )

    @classmethod
    def from_row(cls, row: "TaskRow") -> Task:
        return cls(
            id=row.id,
            split=TaskSplit(row.split),
            specialty=row.specialty,
            title=row.title,
            description=row.description,
            command=tuple(row.command),
            dataset_ref=row.dataset_ref,
            created_by=row.created_by,
            expected_answer=row.expected_answer,
            created_at=row.created_at,
        )
