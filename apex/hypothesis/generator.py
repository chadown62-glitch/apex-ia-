"""`HypothesisGenerator` — génération déterministe, template, aucun modèle.

Compose UNIQUEMENT `apex.weakness.model.WeaknessReport` (réel, Phase 12) —
zéro dépendance à `ModelProvider`. Le registre note « seul composant où un
modèle externe apporte un gain net » : cette étape en reste délibérément
dépourvue (même discipline que `apex.taskgen`, Phase 15 — aucun
raccordement prématuré aux couches supérieures). Une hypothèse plus riche,
pilotée par un modèle, reste `PLANNED`.

Chaque hypothèse nomme un chemin de réfutation concret et déjà réel :
`apex.taskgen.model.GenerationMode.TARGETED_WEAKNESS` (Phase 15) pour
produire les tâches de renforcement, `apex.stattest.StatTest.compare_paired`
(Phase 16) pour juger si l'amélioration observée est distinguable du bruit
— jamais exécutés ici, seulement nommés comme la méthode qui permettrait de
tester la prédiction.
"""

from __future__ import annotations

from apex.hypothesis.model import Hypothesis
from apex.weakness.model import WeaknessCategory, WeaknessReport

_NO_WEAKNESS_CATEGORIES = frozenset({WeaknessCategory.INSUFFICIENT_DATA, WeaknessCategory.NO_WEAKNESS})

_PROPOSED_FALSIFICATION = (
    "Générer un nouvel échantillon d'EvalRun sur la même spécialité après un cycle de "
    "renforcement ciblé (apex.taskgen, GenerationMode.TARGETED_WEAKNESS), puis comparer "
    "les résultats avant/après avec apex.stattest.StatTest.compare_paired — l'hypothèse "
    "est réfutée si aucune amélioration statistiquement significative n'est observée "
    "(StatTestVerdict.NOT_SIGNIFICANT ou une différence non favorable)."
)


class HypothesisGenerator:
    def generate(self, *, specialty: str, weakness_report: WeaknessReport) -> Hypothesis:
        if weakness_report.category in _NO_WEAKNESS_CATEGORIES:
            raise ValueError(
                f"rien à expliquer pour la catégorie « {weakness_report.category.value} » — "
                "aucune faiblesse observée à formuler en hypothèse"
            )

        claim = (
            f"Le domaine « {specialty} » présente une faiblesse "
            f"({weakness_report.category.value}, {weakness_report.reason}) qui peut être "
            "réduite par un renforcement ciblé plutôt que par une exécution supplémentaire "
            "non dirigée."
        )
        prediction = (
            "Après un cycle de renforcement ciblé sur cette faiblesse, le taux d'échec "
            "observé sur un nouvel échantillon sera significativement plus bas que le taux "
            f"actuel ({weakness_report.failure_rate:.0%})."
            if weakness_report.failure_rate is not None
            else "Après un cycle de renforcement ciblé, le taux d'échec observé baissera de manière significative."
        )

        return Hypothesis.new(
            specialty=specialty, claim=claim, prediction=prediction,
            proposed_falsification=_PROPOSED_FALSIFICATION, source_weakness_report=weakness_report,
        )
