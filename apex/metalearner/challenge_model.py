"""`CurriculumChallenge` — représentation structurée d'une VRAIE tâche
d'évaluation/expérience produite depuis une faiblesse réelle (passe
« Adversarial Curriculum + Capability Benchmarking », N+10, §2 de la
mission).

Compose `apex.metalearner.judge_feedback_model.CurriculumTarget` (N+9,
INCHANGÉ) — n'invente rien : un `CurriculumChallenge` référence TOUJOURS un
`apex.tasks.model.Task` RÉELLEMENT persisté (`task_id`), jamais un résultat
attendu fabriqué (§2 : « il ne doit jamais contenir directement un résultat
attendu fabriqué »). `weakness_id` est une clé DÉRIVÉE déterministe
(`f"{specialty}:{dimension.value}:{failure_category.value}"`) — pas une
clé étrangère vers une table de faiblesses (`GauntletWeakness` reste
calculée à la demande, N+9, jamais persistée séparément).

**`ChallengeSource = apex.taskgen.model.TaskKind` (passe « Universal Task +
Curriculum Engine », N+11, § Phase 3 de la mission)** : ALIAS DIRECT, pas
une sous-classe ni un doublon — le vocabulaire « quel générateur/mécanisme
a produit ce challenge » est désormais défini UNE SEULE FOIS, dans
`apex.taskgen` (couche la plus basse, qui doit rester important sans
`apex.metalearner`). Tout code existant qui importe `ChallengeSource` (y
compris `ChallengeSource.EDGE_CASE`/`.ADVERSARIAL`/... et `ChallengeSource
(row.source)`) continue de fonctionner à l'identique — c'est le MÊME objet
`Enum`, seul son import canonique change de module.

**`parent_task_id`/`seed` (N+11, additifs, `None` par défaut)** :
provenance supplémentaire pour un challenge produit par le moteur de
variantes générique (`apex.taskgen.variant_engine.VariantEngine`, § Phase
6/16) — `None` pour un challenge produit par l'ancien chemin N+10
(toujours valide, jamais rétrofitté de force)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity
from apex.taskgen.model import GenerationMode, TaskKind

if TYPE_CHECKING:
    from apex.db.models import CurriculumChallengeRow
    from apex.metalearner.judge_feedback_model import CurriculumTarget

CHALLENGE_PROTOCOL_VERSION = "1"


def _utcnow() -> datetime:
    return datetime.now(UTC)


#: Alias direct — voir la docstring du module. `TaskKind` porte en plus
#: `NORMAL` (absent de l'ancien `ChallengeSource`, jamais utilisé par
#: `CurriculumChallenge.source` avant N+11 puisqu'aucun challenge N+10
#: n'était produit pour une tâche de référence pure).
ChallengeSource = TaskKind


def weakness_key(specialty: str, dimension: JudgeDimension, failure_category: str) -> str:
    """Clé DÉRIVÉE déterministe identifiant « cette faiblesse récurrente »
    — jamais une clé étrangère vers une ligne persistée séparément (§ voir
    la docstring du module)."""
    return f"{specialty}:{dimension.value}:{failure_category}"


#: Sévérité -> difficulté entière — mapping DÉTERMINISTE, documenté, jamais
#: ajusté pour produire un résultat souhaité à l'avance (même discipline
#: qu'`apex.metalearner.weakness_engine._CONFIDENCE_SATURATION_COUNT`).
_SEVERITY_TO_DIFFICULTY = {
    JudgeSeverity.LOW: 1,
    JudgeSeverity.MEDIUM: 2,
    JudgeSeverity.HIGH: 3,
    JudgeSeverity.CRITICAL: 4,
}


def difficulty_from_severity(severity: JudgeSeverity) -> int:
    return _SEVERITY_TO_DIFFICULTY[severity]


#: `GenerationMode` -> `TaskKind` pour les cibles dont le mapping ne dépend
#: QUE du mode recommandé (pas de la dimension) — SECURITY/GENERALIZATION
#: sont résolus séparément ci-dessous (leur `TaskKind` propre ne coïncide
#: avec AUCUN `GenerationMode` dédié, § Phase 3 de la mission N+11 :
#: `GenerationMode` reste inchangé, `TaskKind` est le vocabulaire UNIFIÉ de
#: plus haut niveau).
_MODE_TO_TASK_KIND: dict[GenerationMode, TaskKind] = {
    GenerationMode.REGRESSION: TaskKind.REGRESSION,
    GenerationMode.TARGETED_WEAKNESS: TaskKind.TARGETED_WEAKNESS,
    GenerationMode.EDGE_CASE: TaskKind.EDGE_CASE,
    GenerationMode.ADVERSARIAL: TaskKind.ADVERSARIAL,
}


def resolve_task_kind(target: CurriculumTarget) -> TaskKind | None:
    """Dérive le `TaskKind` UNIFIÉ d'un `CurriculumTarget` (N+9, inchangé)
    — MÊME logique que `apex.metalearner.challenge_service.
    CurriculumChallengeService._build_one` (extraite ici pour être
    réutilisée SANS duplication par `apex.metalearner.curriculum_plan`,
    § Phase 13). `None` si aucun `TaskKind` n'est dérivable (target
    mutation-seule, § docstring d'`apex.metalearner.curriculum_engine`)."""
    if target.recommended_mode is None:
        return None
    if target.dimension is JudgeDimension.SECURITY:
        return TaskKind.SECURITY
    if target.dimension is JudgeDimension.GENERALIZATION:
        return TaskKind.GENERALIZATION
    return _MODE_TO_TASK_KIND.get(target.recommended_mode)


@dataclass(frozen=True)
class CurriculumChallenge:
    id: str
    specialty: str
    weakness_id: str
    dimension: JudgeDimension
    difficulty: int
    generation: int
    task_id: str
    evaluation_criteria: str
    source: ChallengeSource
    heldout: bool
    adversarial: bool
    protocol_version: str
    parent_task_id: str | None = None
    seed: int | None = None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        specialty: str,
        weakness_id: str,
        dimension: JudgeDimension,
        difficulty: int,
        generation: int,
        task_id: str,
        evaluation_criteria: str,
        source: ChallengeSource,
        heldout: bool,
        adversarial: bool,
        parent_task_id: str | None = None,
        seed: int | None = None,
    ) -> CurriculumChallenge:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, weakness_id=weakness_id, dimension=dimension,
            difficulty=difficulty, generation=generation, task_id=task_id,
            evaluation_criteria=evaluation_criteria, source=source, heldout=heldout,
            adversarial=adversarial, protocol_version=CHALLENGE_PROTOCOL_VERSION,
            parent_task_id=parent_task_id, seed=seed,
        )

    @classmethod
    def from_row(cls, row: CurriculumChallengeRow) -> CurriculumChallenge:
        return cls(
            id=row.id, specialty=row.specialty, weakness_id=row.weakness_id,
            dimension=JudgeDimension(row.dimension), difficulty=row.difficulty, generation=row.generation,
            task_id=row.task_id, evaluation_criteria=row.evaluation_criteria,
            source=ChallengeSource(row.source), heldout=row.heldout, adversarial=row.adversarial,
            protocol_version=row.protocol_version, parent_task_id=row.parent_task_id, seed=row.seed,
            created_at=row.created_at,
        )
