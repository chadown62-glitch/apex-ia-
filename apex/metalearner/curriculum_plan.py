"""`CurriculumPlan` — plan DÉTERMINISTE produit par le Curriculum Engine
(passe « Universal Task + Curriculum Engine », N+11, § Phase 13/14 de la
mission).

Ne réimplémente RIEN de la logique de décision déjà réelle
(`apex.metalearner.curriculum_engine.CurriculumEngineService.build_targets`,
N+9) : ce module se contente de GROUPER les `CurriculumTarget` déjà produits
par `TaskKind` (`apex.metalearner.challenge_model.resolve_task_kind`, MÊME
mapping que `apex.metalearner.challenge_service`, aucune duplication), de
les ORDONNER par priorité DÉTERMINISTE (sévérité maximale du groupe
décroissante), puis d'appliquer les plafonds `apex.config.schema.
CurriculumBudgetPolicy` (§ Phase 14) — un excédent est TRONQUÉ
déterministiquement, priorité la plus faible (urgence la plus basse)
éliminée en premier, jamais un dépassement silencieux."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from apex.metalearner.challenge_model import difficulty_from_severity, resolve_task_kind
from apex.metalearner.judge_model import JudgeDimension
from apex.taskgen.difficulty import Difficulty, from_int
from apex.taskgen.model import TaskKind

if TYPE_CHECKING:
    from apex.config.schema import CurriculumBudgetPolicy
    from apex.metalearner.judge_feedback_model import CurriculumTarget

CURRICULUM_PLAN_PROTOCOL_VERSION = "1"

#: `TaskKind` marqués `adversarial=True`/`heldout=True` — même classification
#: qu'`apex.metalearner.challenge_service._ADVERSARIAL_MODES`/
#: `_HELDOUT_DIMENSIONS`, réutilisée ici pour appliquer les SOUS-plafonds
#: correspondants (§14).
_ADVERSARIAL_KINDS = frozenset({TaskKind.ADVERSARIAL, TaskKind.SECURITY})
_HELDOUT_KINDS = frozenset({TaskKind.GENERALIZATION})


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CurriculumPlanItem:
    kind: TaskKind
    #: `None` pour `BUG_FARM` — pas rattaché à une dimension de faiblesse
    #: unique (dérivé d'un ensemble de `BugRecord`, pas d'un
    #: `CurriculumTarget`).
    dimension: JudgeDimension | None
    count: int
    priority: int
    difficulty: Difficulty


@dataclass(frozen=True)
class CurriculumPlan:
    specialty: str
    generation: int
    items: tuple[CurriculumPlanItem, ...]
    total_task_count: int
    truncated: bool
    protocol_version: str = CURRICULUM_PLAN_PROTOCOL_VERSION
    created_at: datetime = field(default_factory=_utcnow)


def _priority_for(max_difficulty: int) -> int:
    """Plus URGENT => priorité PLUS BASSE (0 = le plus urgent) — `CRITICAL`
    (4) -> 0, `LOW` (1) -> 3. Fonction pure de l'entier de difficulté déjà
    réel (`difficulty_from_severity`), jamais un score inventé."""
    return 4 - max_difficulty


class CurriculumPlanBuilder:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def build(
        self,
        specialty: str,
        generation: int,
        targets: tuple[CurriculumTarget, ...],
        *,
        budget: CurriculumBudgetPolicy | None = None,
    ) -> CurriculumPlan:
        grouped: dict[TaskKind, list[CurriculumTarget]] = defaultdict(list)
        for target in targets:
            kind = resolve_task_kind(target)
            if kind is not None:
                grouped[kind].append(target)

        items = [
            CurriculumPlanItem(
                kind=kind, dimension=group[0].dimension, count=len(group),
                priority=_priority_for(max(difficulty_from_severity(t.weakness.severity) for t in group)),
                difficulty=from_int(max(difficulty_from_severity(t.weakness.severity) for t in group)),
            )
            for kind, group in grouped.items()
        ]

        bug_farm_item = self._bug_farm_item(specialty)
        if bug_farm_item is not None:
            items.append(bug_farm_item)

        items.sort(key=lambda item: (item.priority, item.kind.value))

        truncated = False
        if budget is not None:
            items, truncated = _apply_budget(items, budget)

        return CurriculumPlan(
            specialty=specialty, generation=generation, items=tuple(items),
            total_task_count=sum(item.count for item in items), truncated=truncated,
        )

    def _bug_farm_item(self, specialty: str) -> CurriculumPlanItem | None:
        from apex.metalearner.bug_farm_model import BugStatus
        from apex.metalearner.bug_farm_service import BugFarmService

        bugs = BugFarmService(self._session_factory).list_bugs(specialty)
        eligible = [b for b in bugs if b.status in (BugStatus.OPEN, BugStatus.REGRESSED)]
        if not eligible:
            return None
        max_difficulty = max(difficulty_from_severity(b.severity) for b in eligible)
        return CurriculumPlanItem(
            kind=TaskKind.BUG_FARM, dimension=None, count=len(eligible),
            priority=_priority_for(max_difficulty), difficulty=from_int(max_difficulty),
        )


def _apply_budget(
    items: list[CurriculumPlanItem], budget: CurriculumBudgetPolicy,
) -> tuple[list[CurriculumPlanItem], bool]:
    truncated = False
    result: list[CurriculumPlanItem] = []
    adversarial_used = heldout_used = bug_farm_used = total_used = 0
    for item in items:
        count = item.count
        if item.kind in _ADVERSARIAL_KINDS:
            count = min(count, max(budget.max_adversarial_per_generation - adversarial_used, 0))
        if item.kind in _HELDOUT_KINDS:
            count = min(count, max(budget.max_heldout_per_generation - heldout_used, 0))
        if item.kind is TaskKind.BUG_FARM:
            count = min(count, max(budget.max_bug_farm_per_generation - bug_farm_used, 0))
        count = min(count, max(budget.max_tasks_per_generation - total_used, 0))
        if count < item.count:
            truncated = True
        if count <= 0:
            continue
        result.append(replace(item, count=count))
        if item.kind in _ADVERSARIAL_KINDS:
            adversarial_used += count
        if item.kind in _HELDOUT_KINDS:
            heldout_used += count
        if item.kind is TaskKind.BUG_FARM:
            bug_farm_used += count
        total_used += count
    return result, truncated
