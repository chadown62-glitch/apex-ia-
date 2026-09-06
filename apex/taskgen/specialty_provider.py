"""`SpecialtyTaskProvider` — contrat par lequel UNE spécialité déclare
comment elle sait produire des tâches (passe « Universal Task + Curriculum
Engine », N+11, § Phase 4/5 de la mission).

Le `CurriculumEngine`/`ChallengeService` ne doivent plus connaître AUCUN
détail interne d'une spécialité (script diagnostic, fractions de policy,
cibles de sonde...) — ils passent EXCLUSIVEMENT par ce contrat. Une
spécialité peut ne PAS supporter un `TaskKind` : elle le déclare
explicitement `False` dans sa `capability_matrix()` (§5 : « ne jamais
prétendre générer une tâche qu'elle ne sait pas réellement créer »).

`ResearchTaskProvider` est la SEULE implémentation réelle aujourd'hui —
elle ne réimplémente RIEN : elle délègue entièrement à
`apex.taskgen.deterministic.DeterministicTaskGenerator` (inchangé), en
traduisant `TaskKind` (vocabulaire UNIFIÉ, § Phase 3) vers `GenerationMode`
+ les paramètres kind-spécifiques déjà réels (`EdgeCaseKind`/
`AdversarialKind`/`GeneralizationVariant`). `SECURITY` route vers
`AdversarialKind.NETWORK_BOUNDARY_PROBE`/`FILESYSTEM_BOUNDARY_PROBE` (les
deux SEULES frontières de sécurité réellement sondables ici, § Phase 10) ;
`GENERALIZATION` route vers `GenerationMode.NORMAL` avec une
`GeneralizationVariant` — même mapping que `apex.metalearner.
curriculum_engine` (N+10), désormais exposé de façon générique."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

from apex.errors import NotImplementedYet
from apex.taskgen.model import (
    AdversarialKind,
    EdgeCaseKind,
    GeneralizationVariant,
    GeneratedTaskSpec,
    GenerationMode,
    TaskKind,
)
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessReport

if TYPE_CHECKING:
    from apex.sandbox.model import ResourceLimits
    from apex.taskgen.provider import TaskGenerator


class SpecialtyTaskProvider(ABC):
    @property
    @abstractmethod
    def specialty_id(self) -> str: ...

    @abstractmethod
    def capability_matrix(self) -> Mapping[TaskKind, bool]:
        """§ Phase 5 : matrice déclarative — chaque `TaskKind` non listé est
        implicitement `UNSUPPORTED` (voir `supports`)."""

    def supports(self, kind: TaskKind) -> bool:
        return bool(self.capability_matrix().get(kind, False))

    @abstractmethod
    def create(
        self,
        *,
        kind: TaskKind,
        split: TaskSplit = TaskSplit.TRAIN,
        level: int | None = None,
        weakness_report: WeaknessReport | None = None,
        base_task: Task | None = None,
        edge_case_kind: EdgeCaseKind | None = None,
        adversarial_kind: AdversarialKind | None = None,
        generalization_variant: GeneralizationVariant | None = None,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        """Produit une proposition de tâche pour `kind`. Doit lever
        `apex.errors.NotImplementedYet` si `not self.supports(kind)` —
        jamais un contenu inventé pour une capacité non supportée."""

    @abstractmethod
    def create_variant(
        self,
        *,
        parent_task: Task,
        transformation: TaskKind,
        seed: int,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        """§ Phase 6 — moteur de variantes génériques : produit UNE
        variante DÉTERMINISTE de `parent_task` (même tâche + même seed +
        même version de générateur => même résultat ; seed différent =>
        variante VALIDE différente quand plusieurs existent) — jamais une
        copie de la tâche parente."""


_CAPABILITY_MATRIX: dict[TaskKind, bool] = {
    TaskKind.NORMAL: True,
    TaskKind.TARGETED_WEAKNESS: True,
    TaskKind.REGRESSION: True,
    TaskKind.EDGE_CASE: True,
    TaskKind.ADVERSARIAL: True,
    TaskKind.SECURITY: True,
    TaskKind.GENERALIZATION: True,
    TaskKind.BUG_FARM: True,
}

#: Tuples ORDONNÉS, utilisés pour la sélection déterministe par seed
#: (`create_variant`) — l'ORDRE ne change jamais d'une exécution à l'autre
#: (ordre de déclaration de l'énumération, stable).
_EDGE_CASE_KINDS: tuple[EdgeCaseKind, ...] = tuple(EdgeCaseKind)
_ADVERSARIAL_KINDS: tuple[AdversarialKind, ...] = tuple(AdversarialKind)
#: Les DEUX SEULES frontières de sécurité réellement sondables ici (§ Phase
#: 10 de la mission N+11 — même choix que `apex.metalearner.
#: curriculum_engine`, N+10, désormais généralisé aux deux probes).
_SECURITY_ADVERSARIAL_KINDS: tuple[AdversarialKind, ...] = (
    AdversarialKind.NETWORK_BOUNDARY_PROBE,
    AdversarialKind.FILESYSTEM_BOUNDARY_PROBE,
)
_GENERALIZATION_VARIANTS: tuple[GeneralizationVariant, ...] = tuple(GeneralizationVariant)

#: Transformations dont une VARIANTE générique a un sens (§ Phase 6) —
#: `NORMAL`/`TARGETED_WEAKNESS` ne sont jamais des transformations d'une
#: tâche parente, seulement des tâches de référence.
_VARIANT_TRANSFORMATIONS = frozenset(
    {
        TaskKind.EDGE_CASE, TaskKind.ADVERSARIAL, TaskKind.SECURITY, TaskKind.GENERALIZATION,
        TaskKind.REGRESSION, TaskKind.BUG_FARM,
    },
)


class ResearchTaskProvider(SpecialtyTaskProvider):
    def __init__(self, generator: TaskGenerator | None = None) -> None:
        if generator is None:
            from apex.taskgen.provider import get_default_generator

            generator = get_default_generator()
        self._generator = generator

    @property
    def specialty_id(self) -> str:
        return "research"

    def capability_matrix(self) -> dict[TaskKind, bool]:
        return dict(_CAPABILITY_MATRIX)

    def create(
        self,
        *,
        kind: TaskKind,
        split: TaskSplit = TaskSplit.TRAIN,
        level: int | None = None,
        weakness_report: WeaknessReport | None = None,
        base_task: Task | None = None,
        edge_case_kind: EdgeCaseKind | None = None,
        adversarial_kind: AdversarialKind | None = None,
        generalization_variant: GeneralizationVariant | None = None,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        if not self.supports(kind):
            raise NotImplementedYet(
                f"ResearchTaskProvider({kind.value})", phase=15,
                fallback="capacité déclarée UNSUPPORTED (capability_matrix)",
            )
        if kind is TaskKind.NORMAL:
            return self._generator.generate(
                mode=GenerationMode.NORMAL, specialty=self.specialty_id, split=split,
                generalization_variant=generalization_variant,
            )
        if kind is TaskKind.TARGETED_WEAKNESS:
            return self._generator.generate(
                mode=GenerationMode.TARGETED_WEAKNESS, specialty=self.specialty_id, split=split,
                weakness_report=weakness_report,
            )
        if kind is TaskKind.REGRESSION:
            return self._generator.generate(
                mode=GenerationMode.REGRESSION, specialty=self.specialty_id, base_task=base_task,
            )
        if kind is TaskKind.BUG_FARM:
            return self._generator.generate(
                mode=GenerationMode.BUG_FARM, specialty=self.specialty_id, base_task=base_task,
            )
        if kind is TaskKind.EDGE_CASE:
            return self._generator.generate(
                mode=GenerationMode.EDGE_CASE, specialty=self.specialty_id, split=split,
                edge_case_kind=edge_case_kind, limits=limits,
            )
        if kind is TaskKind.ADVERSARIAL:
            return self._generator.generate(
                mode=GenerationMode.ADVERSARIAL, specialty=self.specialty_id, split=split,
                adversarial_kind=adversarial_kind, limits=limits,
            )
        if kind is TaskKind.SECURITY:
            chosen = adversarial_kind or _SECURITY_ADVERSARIAL_KINDS[0]
            if chosen not in _SECURITY_ADVERSARIAL_KINDS:
                allowed = [k.value for k in _SECURITY_ADVERSARIAL_KINDS]
                raise ValueError(
                    f"adversarial_kind={chosen.value} n'est pas une frontière de sécurité "
                    f"reconnue pour TaskKind.SECURITY (attendu : {allowed})",
                )
            return self._generator.generate(
                mode=GenerationMode.ADVERSARIAL, specialty=self.specialty_id, split=split,
                adversarial_kind=chosen, limits=limits,
            )
        if kind is TaskKind.GENERALIZATION:
            variant = generalization_variant or GeneralizationVariant.ALTERNATE_PROBE
            return self._generator.generate(
                mode=GenerationMode.NORMAL, specialty=self.specialty_id, split=split,
                generalization_variant=variant,
            )
        raise NotImplementedYet(f"ResearchTaskProvider({kind.value})", phase=15)  # pragma: no cover

    def create_variant(
        self,
        *,
        parent_task: Task,
        transformation: TaskKind,
        seed: int,
        limits: ResourceLimits | None = None,
    ) -> GeneratedTaskSpec:
        if transformation not in _VARIANT_TRANSFORMATIONS:
            raise ValueError(
                f"TaskKind.{transformation.name} n'est jamais une transformation d'une tâche parente",
            )
        if not self.supports(transformation):
            raise NotImplementedYet(
                f"ResearchTaskProvider.create_variant({transformation.value})", phase=15,
                fallback="capacité déclarée UNSUPPORTED (capability_matrix)",
            )
        if transformation is TaskKind.REGRESSION:
            return self.create(kind=TaskKind.REGRESSION, split=parent_task.split, base_task=parent_task)
        if transformation is TaskKind.BUG_FARM:
            return self.create(kind=TaskKind.BUG_FARM, split=parent_task.split, base_task=parent_task)
        if transformation is TaskKind.EDGE_CASE:
            picked = _EDGE_CASE_KINDS[seed % len(_EDGE_CASE_KINDS)]
            return self.create(
                kind=TaskKind.EDGE_CASE, split=parent_task.split, edge_case_kind=picked, limits=limits,
            )
        if transformation is TaskKind.ADVERSARIAL:
            picked_adv = _ADVERSARIAL_KINDS[seed % len(_ADVERSARIAL_KINDS)]
            return self.create(
                kind=TaskKind.ADVERSARIAL, split=parent_task.split, adversarial_kind=picked_adv,
                limits=limits,
            )
        if transformation is TaskKind.SECURITY:
            picked_sec = _SECURITY_ADVERSARIAL_KINDS[seed % len(_SECURITY_ADVERSARIAL_KINDS)]
            return self.create(
                kind=TaskKind.SECURITY, split=parent_task.split, adversarial_kind=picked_sec, limits=limits,
            )
        # GENERALIZATION : toujours HELDOUT (§6 N+10, reconduit ici — jamais
        # une variante de généralisation utilisée pour du TRAIN).
        picked_variant = _GENERALIZATION_VARIANTS[seed % len(_GENERALIZATION_VARIANTS)]
        return self.create(
            kind=TaskKind.GENERALIZATION, split=TaskSplit.HELDOUT, generalization_variant=picked_variant,
        )
