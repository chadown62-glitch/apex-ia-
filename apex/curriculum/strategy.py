"""`CurriculumStrategy` — règles déterministes, phase 12.

Prend un `apex.weakness.model.WeaknessReport` déjà produit
(`WeaknessDetector`) et décide d'UNE direction (`ADVANCE`/`HOLD`/
`REINFORCE`), jamais plus d'un cran de niveau à la fois. **Aucune
optimisation opaque, aucune dépendance à `MetaLearner`** (`PLANNED`, phase
22) : la politique qui choisira dynamiquement entre plusieurs stratégies de
curriculum reste future — ici, une seule règle fixe, entièrement
reproductible (même `WeaknessReport` -> même `CurriculumDecision`, toujours).
"""

from __future__ import annotations

from apex.curriculum.model import CurriculumDecision, CurriculumDirection
from apex.weakness.model import WeaknessCategory, WeaknessReport


class CurriculumStrategy:
    def __init__(self, *, min_level: int = 1, max_level: int = 10) -> None:
        if min_level < 1:
            raise ValueError("min_level doit être >= 1")
        if max_level < min_level:
            raise ValueError("max_level doit être >= min_level")
        self._min_level = min_level
        self._max_level = max_level

    def decide(
        self, *, specialty: str, current_level: int, weakness_report: WeaknessReport,
    ) -> CurriculumDecision:
        if not (self._min_level <= current_level <= self._max_level):
            raise ValueError(
                f"current_level={current_level} hors bornes [{self._min_level}, {self._max_level}]"
            )

        category = weakness_report.category
        if category is WeaknessCategory.INSUFFICIENT_DATA:
            direction = CurriculumDirection.HOLD
            recommended = current_level
            reason = "données insuffisantes — reste au niveau actuel jusqu'à ce qu'un signal existe"
        elif category is WeaknessCategory.NO_WEAKNESS:
            direction = CurriculumDirection.ADVANCE
            recommended = min(current_level + 1, self._max_level)
            reason = "aucune faiblesse détectée — progression d'un cran"
        elif category is WeaknessCategory.ISOLATED_FAILURE:
            direction = CurriculumDirection.HOLD
            recommended = current_level
            reason = "échec isolé — ne justifie pas un recul, reste au niveau actuel"
        elif category is WeaknessCategory.REPEATED_WEAKNESS:
            direction = CurriculumDirection.HOLD
            recommended = current_level
            reason = (
                "motif répété mais pas encore statistiquement crédible — reste au niveau "
                "actuel, aucun recul prématuré"
            )
        elif category is WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS:
            direction = CurriculumDirection.REINFORCE
            recommended = max(current_level - 1, self._min_level)
            reason = "faiblesse jugée statistiquement crédible — recul d'un cran pour renforcer les prérequis"
        else:  # pragma: no cover — vocabulaire fermé (Enum), garde défensive honnête
            direction = CurriculumDirection.HOLD
            recommended = current_level
            reason = f"catégorie de faiblesse non reconnue ({category!r}) — repli prudent, aucun mouvement"

        return CurriculumDecision(
            specialty=specialty, current_level=current_level, recommended_level=recommended,
            direction=direction, reason=reason, weakness_report=weakness_report,
        )
