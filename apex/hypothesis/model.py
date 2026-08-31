"""Modèle de domaine d'une Hypothesis — dataclass pure, aucune DB.

Registre : `HypothesisGenerator`, « observation -> hypothèse falsifiable »
(`phase_defined` 16, `phase_implemented` 16). « Distinct de l'ExperimentEngine,
qui exécute et décide mais ne propose pas » — une `Hypothesis` est une
PROPOSITION, jamais une exécution : ce module ne lance rien (ni
`TaskGenerator`, ni `EvalRunner`, ni `StatTest`), il ne fait que les NOMMER
dans `proposed_falsification` comme le chemin qui permettrait de la tester.

Volontairement **jamais persistée** (aucune table `hypotheses`) : aucun
consommateur réel (`ExperimentEngine`) n'existe encore pour la lire — créer
son schéma maintenant reviendrait à deviner une implémentation absente
(ADR-0007). `Hypothesis` reste un objet en mémoire, comme
`apex.taskgen.model.GeneratedTaskSpec`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apex.weakness.model import WeaknessReport


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Hypothesis:
    """`prediction` doit toujours pouvoir être réfutée par
    `proposed_falsification` — jamais une affirmation vague qu'aucune
    expérience ne pourrait contredire."""

    id: str
    specialty: str
    claim: str
    prediction: str
    proposed_falsification: str
    source_weakness_report: WeaknessReport
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls, *, specialty: str, claim: str, prediction: str, proposed_falsification: str,
        source_weakness_report: WeaknessReport,
    ) -> Hypothesis:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, claim=claim, prediction=prediction,
            proposed_falsification=proposed_falsification, source_weakness_report=source_weakness_report,
        )
