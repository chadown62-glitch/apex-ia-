"""`CurriculumChallengeService` — traduit un `CurriculumTarget` RÉEL (N+9,
`apex.metalearner.curriculum_engine`) en une VRAIE tâche persistée
(`apex.tasks.service.TaskBankService`) + un `CurriculumChallenge` (§2 de la
mission N+10). Ne réimplémente rien : compose `apex.taskgen.deterministic.
DeterministicTaskGenerator` (déjà réel, y compris `EDGE_CASE`/`ADVERSARIAL`
depuis cette même passe) et `TaskBankService.create` (Phase 5, inchangé).

Idempotent PAR GÉNÉRATION : un `(weakness_id, generation)` déjà servi
n'est jamais re-généré (`list_by_weakness` déjà persisté) — évite de créer
une nouvelle `Task`/ligne `curriculum_challenges` à chaque appel pour LA
MÊME faiblesse à LA MÊME génération (§20 de la mission : « bounded
challenge count »)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apex.metalearner.challenge_model import (
    ChallengeSource,
    CurriculumChallenge,
    difficulty_from_severity,
    weakness_key,
)
from apex.metalearner.judge_feedback_model import CurriculumTarget
from apex.metalearner.judge_model import JudgeDimension
from apex.policy.model import Subject
from apex.taskgen.model import GenerationMode
from apex.tasks.model import TaskSplit

if TYPE_CHECKING:
    from apex.metalearner.challenge_repository import CurriculumChallengeRepository
    from apex.sandbox.model import ResourceLimits

#: Dimensions dont le challenge produit est marqué `heldout=True` — §6 de
#: la mission (généralisation) et les tâches HELDOUT réutilisées pour
#: SECURITY/EFFICIENCY/ROBUSTNESS restent TRAIN (elles alimentent
#: l'entraînement/l'orientation, pas la mesure indépendante — voir
#: `apex.metalearner.capability_benchmark_service` pour la VRAIE séparation
#: HELDOUT du benchmark de capacité, §12).
_HELDOUT_DIMENSIONS = frozenset({JudgeDimension.GENERALIZATION})

#: Dimensions dont le challenge produit est marqué `adversarial=True`.
_ADVERSARIAL_MODES = frozenset({GenerationMode.ADVERSARIAL})

_MODE_TO_SOURCE = {
    GenerationMode.REGRESSION: ChallengeSource.REGRESSION,
    GenerationMode.TARGETED_WEAKNESS: ChallengeSource.TARGETED_WEAKNESS,
    GenerationMode.EDGE_CASE: ChallengeSource.EDGE_CASE,
    GenerationMode.ADVERSARIAL: ChallengeSource.ADVERSARIAL,
}


class CurriculumChallengeService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def build_challenges(
        self, specialty: str, generation: int, *, limits: ResourceLimits | None = None,
    ) -> tuple[CurriculumChallenge, ...]:
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository
        from apex.metalearner.curriculum_engine import CurriculumEngineService

        targets = CurriculumEngineService(self._session_factory).build_targets(specialty)
        repo = CurriculumChallengeRepository(self._session_factory)
        created: list[CurriculumChallenge] = []
        for target in targets:
            challenge = self._build_one(target, generation, limits, repo)
            if challenge is not None:
                created.append(challenge)
        return tuple(created)

    def _build_one(
        self,
        target: CurriculumTarget,
        generation: int,
        limits: ResourceLimits | None,
        repo: CurriculumChallengeRepository,
    ) -> CurriculumChallenge | None:
        if target.recommended_mode is None:
            return None
        # SECURITY et GENERALIZATION recommandent respectivement ADVERSARIAL
        # et NORMAL (avec generalization_variant) — leur ChallengeSource
        # propre n'est pas dans `_MODE_TO_SOURCE` par mode seul.
        source: ChallengeSource | None
        if target.dimension is JudgeDimension.SECURITY:
            source = ChallengeSource.SECURITY
        elif target.dimension is JudgeDimension.GENERALIZATION:
            source = ChallengeSource.GENERALIZATION
        else:
            source = _MODE_TO_SOURCE.get(target.recommended_mode)
        if source is None:
            return None

        key = weakness_key(target.specialty, target.dimension, target.weakness.failure_category.value)
        existing = repo.list_by_weakness(key, limit=200)
        if any(c.generation == generation for c in existing):
            return None  # §20 : jamais un doublon pour la même (faiblesse, génération).

        task = self._generate_and_persist_task(target, limits)
        if task is None:
            return None

        heldout = target.dimension in _HELDOUT_DIMENSIONS
        adversarial = target.recommended_mode in _ADVERSARIAL_MODES
        challenge = CurriculumChallenge.new(
            specialty=target.specialty, weakness_id=key, dimension=target.dimension,
            difficulty=difficulty_from_severity(target.weakness.severity), generation=generation,
            task_id=task.id, evaluation_criteria=target.rationale, source=source,
            heldout=heldout, adversarial=adversarial,
        )
        return repo.create(challenge)

    def _generate_and_persist_task(self, target: CurriculumTarget, limits: ResourceLimits | None) -> Any:
        from apex.taskgen.provider import get_default_generator
        from apex.tasks.service import TaskBankService

        generator = get_default_generator()
        split = TaskSplit.HELDOUT if target.dimension in _HELDOUT_DIMENSIONS else TaskSplit.TRAIN
        try:
            if target.recommended_mode is GenerationMode.REGRESSION:
                if target.base_task_id is None:
                    return None
                from apex.tasks.repository import TaskRepository

                base_task = TaskRepository(self._session_factory).get(target.base_task_id)
                if base_task is None:
                    return None
                spec = generator.generate(
                    mode=GenerationMode.REGRESSION, specialty=target.specialty, base_task=base_task,
                )
            elif target.recommended_mode is GenerationMode.EDGE_CASE:
                spec = generator.generate(
                    mode=GenerationMode.EDGE_CASE, specialty=target.specialty, split=split,
                    edge_case_kind=target.edge_case_kind, limits=limits,
                )
            elif target.recommended_mode is GenerationMode.ADVERSARIAL:
                spec = generator.generate(
                    mode=GenerationMode.ADVERSARIAL, specialty=target.specialty, split=split,
                    adversarial_kind=target.adversarial_kind, limits=limits,
                )
            elif target.recommended_mode is GenerationMode.NORMAL:
                spec = generator.generate(
                    mode=GenerationMode.NORMAL, specialty=target.specialty, split=split,
                    generalization_variant=target.generalization_variant,
                )
            else:
                return None
        except Exception:
            # §17 : jamais laisser une panne de génération de tâche
            # remonter et casser la boucle d'évolution — un challenge
            # manqué reste observable (aucune ligne créée), jamais fatal.
            return None

        return TaskBankService(self._session_factory).create(
            split=spec.split, specialty=spec.specialty, title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, dataset_ref=spec.dataset_ref,
        )
