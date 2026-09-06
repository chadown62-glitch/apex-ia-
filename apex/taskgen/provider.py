"""Contrat `TaskGenerator` — même patron que `apex.oracles.provider`/
`apex.judge.provider` : un contrat abstrait minimal, un seul point de choix
d'implémentation (`get_default_generator`), rien d'autre.

Le contrat accepte déjà `weakness_report`/`level`/`base_task` en paramètres
optionnels — pour que les modes non encore réels (`TARGETED_WEAKNESS`
partiel, `BUG_FARM`, ...) n'exigent pas de changer la signature le jour où
ils deviennent réels.

**`edge_case_kind`/`adversarial_kind`/`generalization_variant`/`limits`**
(passe « Adversarial Curriculum + Capability Benchmarking », N+10, additifs
— tous par défaut `None`, aucun appelant existant affecté) : paramètres
requis SEULEMENT par `GenerationMode.EDGE_CASE`/`ADVERSARIAL`, désormais
réels (voir `apex.taskgen.model`). `limits` (les `ResourceLimits` déjà
RÉSOLUS pour CE candidat, `apex.genome.clone_config.resolve_resource_limits`,
inchangé) permet à un adversarial de borner sa pression à la politique
RÉELLEMENT applicable — jamais une valeur devinée ou dépassant cette
politique."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from apex.taskgen.model import (
    AdversarialKind,
    EdgeCaseKind,
    GeneralizationVariant,
    GeneratedTaskSpec,
    GenerationMode,
)
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessReport

if TYPE_CHECKING:
    from apex.sandbox.model import ResourceLimits


class TaskGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        *,
        mode: GenerationMode,
        specialty: str,
        split: TaskSplit = TaskSplit.TRAIN,
        level: int | None = None,
        weakness_report: WeaknessReport | None = None,
        base_task: Task | None = None,
        edge_case_kind: EdgeCaseKind | None = None,
        adversarial_kind: AdversarialKind | None = None,
        generalization_variant: GeneralizationVariant | None = None,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        """Produit une proposition de tâche. Lève `apex.errors.NotImplementedYet`
        pour tout `(mode, specialty)` sans backend réel — jamais un contenu
        inventé pour combler l'absence de dépendance."""


def get_default_generator() -> TaskGenerator:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un générateur plus riche (piloté par
    `ModelProvider`, `CodeIntelligence`, ...) serait choisi une fois
    réellement implémenté. Aujourd'hui : `DeterministicTaskGenerator`, le
    seul générateur réel."""
    from apex.taskgen.deterministic import DeterministicTaskGenerator

    return DeterministicTaskGenerator()
