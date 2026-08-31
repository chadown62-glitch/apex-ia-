"""Modèle de domaine d'un EvalRun — dataclass pure, aucune dépendance DB
(même séparation que `apex.tasks.model.Task` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus

if TYPE_CHECKING:
    # Import différé — même raison que Task.from_row : apex.evalrunner doit
    # rester importable sans l'extra `db` installé.
    from apex.db.models import EvalRunRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvalMethod(str, Enum):
    """Vocabulaire fermé de `docs/COGNITIVE_ARCHITECTURE.md` §5 — repris
    verbatim, pas réinventé ici. **Collecte pure à ce stade** : aucune
    comparaison entre méthodes n'est effectuée par ce module — cette
    comparaison reste `MetaLearner` (`PLANNED`, phase 22), une fois assez
    d'essais accumulés par couple (domaine, méthode)."""

    PRACTICE = "practice"
    SPACED_REPETITION = "spaced_repetition"
    PROBLEM_GENERATION = "problem_generation"
    RESEARCH = "research"
    IMITATION = "imitation"
    SELF_EXPLANATION = "self_explanation"
    TEACHING = "teaching"
    EXPERIMENTATION = "experimentation"
    COMBINATION = "combination"


@dataclass
class EvalRun:
    """Le résultat d'UNE tentative d'UN clone sur UNE tâche. INSERT seul,
    positif ou négatif retenu de façon identique — voir `EvalRunRow` pour la
    garantie SQL. `experiment_ref` est une étiquette libre de regroupement
    (aucune logique associée ici), affordance pour une future phase 16
    (ExperimentEngine) — voir `EvalRunRow` pour le raisonnement complet."""

    id: str
    task_id: str
    clone_id: str
    method: EvalMethod
    oracle_verdict: OracleVerdict
    oracle_detail: str | None
    sandbox_status: ExecutionStatus
    exit_code: int | None
    elapsed_s: float | None
    facts: dict[str, Any]
    created_by: str
    seed: int | None = None
    dataset_ref: str | None = None
    experiment_ref: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        task_id: str,
        clone_id: str,
        method: EvalMethod,
        oracle_verdict: OracleVerdict,
        oracle_detail: str | None,
        sandbox_status: ExecutionStatus,
        exit_code: int | None,
        elapsed_s: float | None,
        facts: dict[str, Any],
        created_by: str,
        seed: int | None = None,
        dataset_ref: str | None = None,
        experiment_ref: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> EvalRun:
        return cls(
            id=str(uuid.uuid4()),
            task_id=task_id,
            clone_id=clone_id,
            method=method,
            oracle_verdict=oracle_verdict,
            oracle_detail=oracle_detail,
            sandbox_status=sandbox_status,
            exit_code=exit_code,
            elapsed_s=elapsed_s,
            facts=facts,
            created_by=created_by,
            seed=seed,
            dataset_ref=dataset_ref,
            experiment_ref=experiment_ref,
            started_at=started_at,
            completed_at=completed_at,
        )

    @classmethod
    def from_row(cls, row: "EvalRunRow") -> EvalRun:
        return cls(
            id=row.id,
            task_id=row.task_id,
            clone_id=row.clone_id,
            method=EvalMethod(row.method),
            oracle_verdict=OracleVerdict(row.oracle_verdict),
            oracle_detail=row.oracle_detail,
            sandbox_status=ExecutionStatus(row.sandbox_status),
            exit_code=row.exit_code,
            elapsed_s=row.elapsed_s,
            facts=dict(row.facts or {}),
            created_by=row.created_by,
            seed=row.seed,
            dataset_ref=row.dataset_ref,
            experiment_ref=row.experiment_ref,
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
        )
