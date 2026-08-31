"""Vocabulaire fermé de `CurriculumStrategy` — `CurriculumDirection`,
`CurriculumDecision`.

Registre : `CurriculumStrategy`, « progression de difficulté »
(`phase_defined` 5, `phase_implemented` 12). Le niveau de difficulté est un
**entier abstrait** (`1..max_level`), délibérément PAS un champ ajouté à
`apex.tasks.model.Task` (aucune abstraction déjà validée n'est modifiée par
cette phase) — la correspondance « niveau N -> quelles tâches concrètes »
reste `PLANNED`, propriété future de `TaskGenerator` (phase 15). Cette
étape ne fait que DÉCIDER d'une direction, jamais choisir de tâches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apex.weakness.model import WeaknessReport


class CurriculumDirection(str, Enum):
    #: Passer à un niveau plus difficile.
    ADVANCE = "advance"
    #: Rester au niveau actuel (données insuffisantes, ou échec isolé qui ne
    #: justifie pas encore un recul).
    HOLD = "hold"
    #: Reculer vers un niveau plus facile (faiblesse jugée crédible).
    REINFORCE = "reinforce"


@dataclass(frozen=True)
class CurriculumDecision:
    """`recommended_level` ne s'écarte JAMAIS de `current_level` de plus
    d'un cran (`docs` — pas de saut incohérent de difficulté) : une
    politique déterministe et prudente, pas encore apprise
    (`MetaLearner`, `PLANNED`, phase 22)."""

    specialty: str
    current_level: int
    recommended_level: int
    direction: CurriculumDirection
    reason: str
    weakness_report: WeaknessReport
