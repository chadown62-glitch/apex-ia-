"""`CurriculumChallengeService` — traduit un `CurriculumTarget` RÉEL (N+9,
`apex.metalearner.curriculum_engine`) en une VRAIE tâche persistée
(`apex.tasks.service.TaskBankService`) + un `CurriculumChallenge` (§2 de la
mission N+10). Ne réimplémente rien : compose `apex.taskgen.task_factory.
UniversalTaskFactory` (passe « Universal Task + Curriculum Engine », N+11 —
voir plus bas) et `TaskBankService.create` (Phase 5, inchangé).

Idempotent PAR GÉNÉRATION : un `(weakness_id, generation)` déjà servi
n'est jamais re-généré (`list_by_weakness` déjà persisté) — évite de créer
une nouvelle `Task`/ligne `curriculum_challenges` à chaque appel pour LA
MÊME faiblesse à LA MÊME génération (§20 de la mission N+10 : « bounded
challenge count »).

**Universal Task Engine (N+11, § Phase 2/4/17)** : ce service ne connaît
plus AUCUN détail interne de la spécialité `research` (script diagnostic,
fractions de policy...) — il construit un `apex.taskgen.task_factory.
TaskSpec` générique et délègue à `UniversalTaskFactory.create`, qui route
lui-même vers le `SpecialtyTaskProvider` réel de `target.specialty`
(`apex.taskgen.registry`). `research` passe donc RÉELLEMENT par ce
contrat générique — la preuve que l'abstraction fonctionne, pas un simple
renommage (§17 de la mission : « ne pas simplement renommer le code
actuel »).

**Budget (N+11, § Phase 14)** : `build_challenges` accepte un `budget`
optionnel (`apex.config.schema.CurriculumBudgetPolicy`) — les cibles sont
groupées par `TaskKind` (`apex.metalearner.challenge_model.
resolve_task_kind`) et TRONQUÉES selon le `apex.metalearner.
curriculum_plan.CurriculumPlan` correspondant (priorité la plus faible
éliminée en premier) avant toute génération — jamais un dépassement des
plafonds administrateur."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apex.metalearner.challenge_model import (
    CurriculumChallenge,
    difficulty_from_severity,
    resolve_task_kind,
    weakness_key,
)
from apex.metalearner.judge_feedback_model import CurriculumTarget
from apex.metalearner.judge_model import JudgeDimension
from apex.policy.model import Subject
from apex.taskgen.model import TaskKind
from apex.tasks.model import TaskSplit

if TYPE_CHECKING:
    from apex.config.schema import CurriculumBudgetPolicy
    from apex.metalearner.challenge_repository import CurriculumChallengeRepository
    from apex.sandbox.model import ResourceLimits

#: Dimensions dont le challenge produit est marqué `heldout=True` — §6 de
#: la mission N+10 (généralisation) et les tâches HELDOUT réutilisées pour
#: SECURITY/EFFICIENCY/ROBUSTNESS restent TRAIN (elles alimentent
#: l'entraînement/l'orientation, pas la mesure indépendante — voir
#: `apex.metalearner.capability_benchmark_service` pour la VRAIE séparation
#: HELDOUT du benchmark de capacité, §12).
_HELDOUT_DIMENSIONS = frozenset({JudgeDimension.GENERALIZATION})

#: `TaskKind` marqués `adversarial=True`.
_ADVERSARIAL_KINDS = frozenset({TaskKind.ADVERSARIAL, TaskKind.SECURITY})


class CurriculumChallengeService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def build_challenges(
        self,
        specialty: str,
        generation: int,
        *,
        limits: ResourceLimits | None = None,
        budget: CurriculumBudgetPolicy | None = None,
    ) -> tuple[CurriculumChallenge, ...]:
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository
        from apex.metalearner.curriculum_engine import CurriculumEngineService

        targets = CurriculumEngineService(self._session_factory).build_targets(specialty)
        allowed_counts = self._allowed_counts_per_kind(specialty, generation, targets, budget)

        repo = CurriculumChallengeRepository(self._session_factory)
        created: list[CurriculumChallenge] = []
        used: dict[TaskKind, int] = {}
        # Ordre déterministe : sévérité décroissante puis dimension —
        # même tri que `apex.metalearner.weakness_engine.aggregate`, pour
        # que la troncature par budget élimine toujours les MÊMES cibles
        # à données égales (§14 : jamais un contournement silencieux).
        for target in sorted(
            targets, key=lambda t: (-difficulty_from_severity(t.weakness.severity), t.dimension.value),
        ):
            kind = resolve_task_kind(target)
            if kind is None:
                continue
            cap = allowed_counts.get(kind)
            if cap is not None and used.get(kind, 0) >= cap:
                continue  # § Phase 14 : plafond de budget atteint pour ce TaskKind.
            challenge = self._build_one(target, kind, generation, limits, repo)
            if challenge is not None:
                created.append(challenge)
                used[kind] = used.get(kind, 0) + 1
        return tuple(created)

    def _allowed_counts_per_kind(
        self,
        specialty: str,
        generation: int,
        targets: tuple[CurriculumTarget, ...],
        budget: CurriculumBudgetPolicy | None,
    ) -> dict[TaskKind, int]:
        """`{}` (jamais de plafond appliqué) quand `budget is None` — sinon
        un plafond EXPLICITE pour CHAQUE `TaskKind` réellement candidat
        cette génération, `0` inclus (§14 : un `TaskKind` totalement
        TRONQUÉ par le budget doit rester à `0`, jamais retomber sur
        « aucun plafond connu » = illimité — c'est le bug qu'un simple
        `dict.get` sur `plan.items` aurait silencieusement réintroduit,
        puisqu'un item à `count=0` est absent de `CurriculumPlan.items`)."""
        if budget is None:
            return {}
        from apex.metalearner.curriculum_plan import CurriculumPlanBuilder

        candidate_kinds = {kind for kind in (resolve_task_kind(t) for t in targets) if kind is not None}
        builder = CurriculumPlanBuilder(self._session_factory)
        plan = builder.build(specialty, generation, targets, budget=budget)
        allowed = dict.fromkeys(candidate_kinds, 0)
        allowed.update({item.kind: item.count for item in plan.items if item.kind is not TaskKind.BUG_FARM})
        return allowed

    def _build_one(
        self,
        target: CurriculumTarget,
        kind: TaskKind,
        generation: int,
        limits: ResourceLimits | None,
        repo: CurriculumChallengeRepository,
    ) -> CurriculumChallenge | None:
        key = weakness_key(target.specialty, target.dimension, target.weakness.failure_category.value)
        existing = repo.list_by_weakness(key, limit=200)
        if any(c.generation == generation for c in existing):
            return None  # §20 : jamais un doublon pour la même (faiblesse, génération).

        task = self._generate_and_persist_task(target, kind, limits)
        if task is None:
            return None

        heldout = target.dimension in _HELDOUT_DIMENSIONS
        adversarial = kind in _ADVERSARIAL_KINDS
        # REGRESSION : le `parent_task_id` est la tâche RÉELLE reproduite
        # (§16 de la mission N+11 : provenance traçable) — `None` pour
        # tout autre `TaskKind` de cette voie (pas une variante d'un
        # parent, voir `apex.taskgen.variant_engine` pour ce cas).
        parent_task_id = target.base_task_id if kind is TaskKind.REGRESSION else None
        challenge = CurriculumChallenge.new(
            specialty=target.specialty, weakness_id=key, dimension=target.dimension,
            difficulty=difficulty_from_severity(target.weakness.severity), generation=generation,
            task_id=task.id, evaluation_criteria=target.rationale, source=kind,
            heldout=heldout, adversarial=adversarial, parent_task_id=parent_task_id,
        )
        return repo.create(challenge)

    def _generate_and_persist_task(
        self, target: CurriculumTarget, kind: TaskKind, limits: ResourceLimits | None,
    ) -> Any:
        from apex.taskgen.task_factory import TaskSpec, UniversalTaskFactory
        from apex.tasks.service import TaskBankService

        split = TaskSplit.HELDOUT if target.dimension in _HELDOUT_DIMENSIONS else TaskSplit.TRAIN
        base_task = None
        if kind is TaskKind.REGRESSION:
            if target.base_task_id is None:
                return None
            from apex.tasks.repository import TaskRepository

            base_task = TaskRepository(self._session_factory).get(target.base_task_id)
            if base_task is None:
                return None

        failure_category = target.weakness.failure_category.value
        weakness_id = weakness_key(target.specialty, target.dimension, failure_category)
        spec = TaskSpec(
            kind=kind, specialty=target.specialty, split=split, base_task=base_task,
            edge_case_kind=target.edge_case_kind, adversarial_kind=target.adversarial_kind,
            generalization_variant=target.generalization_variant, limits=limits, weakness_id=weakness_id,
        )
        try:
            generated = UniversalTaskFactory().create(spec)
        except Exception:
            # §17 de la mission N+9 (reconduit) : jamais laisser une panne
            # de génération de tâche remonter et casser la boucle
            # d'évolution — un challenge manqué reste observable (aucune
            # ligne créée), jamais fatal.
            return None

        return TaskBankService(self._session_factory).create(
            split=generated.split, specialty=generated.specialty, title=generated.title,
            description=generated.description, command=generated.command, created_by=Subject.HUMAN,
            dataset_ref=generated.dataset_ref,
        )
