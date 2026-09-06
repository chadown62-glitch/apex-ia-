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
calculée à la demande, N+9, jamais persistée séparément)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

if TYPE_CHECKING:
    from apex.db.models import CurriculumChallengeRow

CHALLENGE_PROTOCOL_VERSION = "1"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChallengeSource(str, Enum):
    """D'où provient CE challenge — répond à « quel générateur/mécanisme
    l'a produit », distinct de `dimension` (« quoi cible-t-il »)."""

    TARGETED_WEAKNESS = "targeted_weakness"
    REGRESSION = "regression"
    EDGE_CASE = "edge_case"
    ADVERSARIAL = "adversarial"
    SECURITY = "security"
    GENERALIZATION = "generalization"
    BUG_FARM = "bug_farm"


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
    ) -> CurriculumChallenge:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, weakness_id=weakness_id, dimension=dimension,
            difficulty=difficulty, generation=generation, task_id=task_id,
            evaluation_criteria=evaluation_criteria, source=source, heldout=heldout,
            adversarial=adversarial,
            protocol_version=CHALLENGE_PROTOCOL_VERSION,
        )

    @classmethod
    def from_row(cls, row: CurriculumChallengeRow) -> CurriculumChallenge:
        return cls(
            id=row.id, specialty=row.specialty, weakness_id=row.weakness_id,
            dimension=JudgeDimension(row.dimension), difficulty=row.difficulty, generation=row.generation,
            task_id=row.task_id, evaluation_criteria=row.evaluation_criteria,
            source=ChallengeSource(row.source), heldout=row.heldout, adversarial=row.adversarial,
            protocol_version=row.protocol_version, created_at=row.created_at,
        )
