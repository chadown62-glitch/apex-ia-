"""`TaskSpec`/`UniversalTaskFactory` — le point d'entrée UNIQUE « créer une
tâche » (passe « Universal Task + Curriculum Engine », N+11, § Phase 2 de
la mission).

Préfère `create(spec)` à sept méthodes obligatoires (`create_normal()`,
`create_edge_case()`, ...) — une seule méthode générique, dispatchée par
`TaskSpec.kind` (`apex.taskgen.model.TaskKind`, § Phase 3), déléguée au
`SpecialtyTaskProvider` RÉEL de `spec.specialty` (`apex.taskgen.registry`,
§ Phase 4) — jamais un second mécanisme de génération : ce module ne fait
QUE router vers le contrat déjà réel, il ne réimplémente rien."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from apex.errors import NotImplementedYet
from apex.taskgen.model import (
    AdversarialKind,
    EdgeCaseKind,
    GeneralizationVariant,
    GeneratedTaskSpec,
    TaskKind,
    TaskProvenance,
)
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessReport

if TYPE_CHECKING:
    from apex.sandbox.model import ResourceLimits

#: Version de CE contrat de fabrication — distincte de
#: `CHALLENGE_PROTOCOL_VERSION` (apex.metalearner.challenge_model) : celle-ci
#: décrit le format de `CurriculumChallenge`, celle-là le comportement de
#: `UniversalTaskFactory.create`. Incrémentée si le mapping `TaskKind` ->
#: comportement de génération change un jour de façon non rétrocompatible.
TASK_FACTORY_PROTOCOL_VERSION = "1"


@dataclass(frozen=True)
class TaskSpec:
    """Requête structurée « produis-moi une tâche » — remplace la
    dispersion de kwargs optionnels d'`apex.taskgen.provider.TaskGenerator.
    generate` par un seul objet explicite, sans changer son comportement
    (`UniversalTaskFactory.create` délègue exactement aux mêmes paramètres)."""

    kind: TaskKind
    specialty: str
    split: TaskSplit = TaskSplit.TRAIN
    level: int | None = None
    weakness_report: WeaknessReport | None = None
    base_task: Task | None = None
    edge_case_kind: EdgeCaseKind | None = None
    adversarial_kind: AdversarialKind | None = None
    generalization_variant: GeneralizationVariant | None = None
    limits: ResourceLimits | None = None
    #: Renseignés par l'appelant quand connus (§16 : provenance traçable) —
    #: reportés tels quels sur `GeneratedTaskSpec.provenance`.
    weakness_id: str | None = None
    challenge_id: str | None = None
    bug_record_id: str | None = None


class UniversalTaskFactory:
    """§ Phase 2 — un seul point d'entrée, quelle que soit la spécialité.
    Lève `apex.errors.NotImplementedYet` si aucun `SpecialtyTaskProvider`
    réel n'existe pour `spec.specialty`, ou si le provider déclare `kind`
    `UNSUPPORTED` — jamais un contenu inventé pour combler l'absence."""

    def create(self, spec: TaskSpec) -> GeneratedTaskSpec:
        from apex.taskgen.registry import get_provider

        provider = get_provider(spec.specialty)
        if provider is None:
            raise NotImplementedYet(
                f"UniversalTaskFactory pour la spécialité « {spec.specialty} »", phase=15,
                fallback="aucun SpecialtyTaskProvider réel enregistré (apex.taskgen.registry)",
            )
        if not provider.supports(spec.kind):
            raise NotImplementedYet(
                f"UniversalTaskFactory({spec.specialty}, {spec.kind.value})", phase=15,
                fallback="capacité déclarée UNSUPPORTED par ce provider (capability_matrix)",
            )
        generated = provider.create(
            kind=spec.kind, split=spec.split, level=spec.level, weakness_report=spec.weakness_report,
            base_task=spec.base_task, edge_case_kind=spec.edge_case_kind,
            adversarial_kind=spec.adversarial_kind, generalization_variant=spec.generalization_variant,
            limits=spec.limits,
        )
        provenance = TaskProvenance(
            weakness_id=spec.weakness_id,
            parent_task_id=spec.base_task.id if spec.base_task is not None else None,
            challenge_id=spec.challenge_id, bug_record_id=spec.bug_record_id, benchmark_id=None,
            generator_version=TASK_FACTORY_PROTOCOL_VERSION, seed=None,
        )
        return GeneratedTaskSpec(
            title=generated.title, description=generated.description, command=generated.command,
            specialty=generated.specialty, split=generated.split, mode=generated.mode,
            dataset_ref=generated.dataset_ref, provenance=provenance,
        )
