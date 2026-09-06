"""Modèle de domaine d'une session d'apprentissage multi-génération —
dataclass pure, aucune DB (passe « Autonomous Multi-Generation Learning
Orchestrator »).

Une session ENVELOPPE des appels répétés à `apex.metalearner.evolution.
PopulationEvolutionService.evolve_generation` — elle ne porte que la
politique de la boucle (combien de générations, où elle en est, pourquoi
elle s'est arrêtée), jamais une seconde copie de l'état de population
(`apex.metalearner.population_model.PopulationMember` reste l'unique
source de vérité pour les membres/générations/lignage)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apex.db.models import LearningSessionRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningSessionStatus(str, Enum):
    RUNNING = "running"
    #: Transitoire — demandée, vérifiée entre deux générations par
    #: `LearningOrchestrator._run_loop`, jamais une interruption en cours
    #: de génération (§7 de la mission : honnête sur la granularité réelle
    #: de l'annulation).
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            LearningSessionStatus.COMPLETED, LearningSessionStatus.FAILED, LearningSessionStatus.CANCELLED,
        )


@dataclass
class LearningSession:
    id: str
    specialty: str
    status: LearningSessionStatus
    max_generations: int
    created_by: str
    generations_completed: int = 0
    convergence_patience: int = 0
    generations_since_improvement: int = 0
    best_member_id: str | None = None
    best_fitness: float | None = None
    best_genome_version_id: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None

    @classmethod
    def new(
        cls, *, specialty: str, max_generations: int, created_by: str, convergence_patience: int = 0,
    ) -> LearningSession:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, status=LearningSessionStatus.RUNNING,
            max_generations=max_generations, created_by=created_by, convergence_patience=convergence_patience,
        )

    @classmethod
    def from_row(cls, row: "LearningSessionRow") -> LearningSession:
        return cls(
            id=row.id, specialty=row.specialty, status=LearningSessionStatus(row.status),
            max_generations=row.max_generations, created_by=row.created_by,
            generations_completed=row.generations_completed, convergence_patience=row.convergence_patience,
            generations_since_improvement=row.generations_since_improvement,
            best_member_id=row.best_member_id, best_fitness=row.best_fitness,
            best_genome_version_id=row.best_genome_version_id, stop_reason=row.stop_reason, error=row.error,
            created_at=row.created_at, completed_at=row.completed_at,
        )
