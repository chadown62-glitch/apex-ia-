"""`VariantEngine` — moteur de variantes GÉNÉRIQUE (passe « Universal Task +
Curriculum Engine », N+11, § Phase 6 de la mission).

« À partir d'une tâche valide Task A, produire A-edge/A-adversarial/
A-generalized/A-regression sans copier la tâche originale » — délègue
ENTIÈREMENT à `SpecialtyTaskProvider.create_variant` (§4), qui seul connaît
comment transformer RÉELLEMENT une tâche de sa spécialité. Ce module ne
fait qu'orchestrer : résoudre le provider, appeler `create_variant`,
attacher la provenance (`parent_task_id`/`seed`/`transformation type`).

**Déterminisme (§6)** : `(parent_task.id, transformation, seed,
generator_version)` identiques => même résultat — garanti PAR
CONSTRUCTION, puisque `ResearchTaskProvider.create_variant` sélectionne sa
variante par `seed % len(kinds_ordonnés)`, une fonction pure. Un `seed`
différent produit une variante VALIDE différente dès que
`len(kinds_ordonnés) > 1` pour cette transformation (vrai pour
`EDGE_CASE`/`ADVERSARIAL`/`SECURITY`/`GENERALIZATION` ; `REGRESSION`/
`BUG_FARM` reproduisent TOUJOURS la même commande quel que soit le seed —
c'est leur définition même, jamais une famille de variantes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apex.errors import NotImplementedYet
from apex.taskgen.model import GeneratedTaskSpec, TaskKind, TaskProvenance

if TYPE_CHECKING:
    from apex.sandbox.model import ResourceLimits
    from apex.tasks.model import Task

VARIANT_ENGINE_PROTOCOL_VERSION = "1"


@dataclass(frozen=True)
class VariantRequest:
    parent_task: Task
    transformation: TaskKind
    seed: int
    parameters: dict[str, str] = field(default_factory=dict)
    #: Provenance uniquement — n'affecte JAMAIS la génération elle-même
    #: (aucune capacité supplémentaire débloquée par un `challenge_id`
    #: fourni ici, seulement de la traçabilité, § Phase 16).
    weakness_id: str | None = None
    challenge_id: str | None = None
    bug_record_id: str | None = None


class VariantEngine:
    def generate(
        self, request: VariantRequest, *, limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        from apex.taskgen.registry import get_provider

        provider = get_provider(request.parent_task.specialty)
        if provider is None:
            raise NotImplementedYet(
                f"VariantEngine pour la spécialité « {request.parent_task.specialty} »", phase=15,
                fallback="aucun SpecialtyTaskProvider réel enregistré (apex.taskgen.registry)",
            )
        generated = provider.create_variant(
            parent_task=request.parent_task, transformation=request.transformation, seed=request.seed,
            limits=limits,
        )
        provenance = TaskProvenance(
            weakness_id=request.weakness_id, parent_task_id=request.parent_task.id,
            challenge_id=request.challenge_id, bug_record_id=request.bug_record_id, benchmark_id=None,
            generator_version=VARIANT_ENGINE_PROTOCOL_VERSION, seed=request.seed,
        )
        return GeneratedTaskSpec(
            title=generated.title, description=generated.description, command=generated.command,
            specialty=generated.specialty, split=generated.split, mode=generated.mode,
            dataset_ref=generated.dataset_ref, provenance=provenance,
        )
