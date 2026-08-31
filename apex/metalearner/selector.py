"""`MetaLearner` — v1, politique STATIQUE, déterministe, jamais apprise.

Registre : `MetaLearner`, « apprendre comment apprendre » (`phase_defined`
5, `phase_implemented` 22). Conforme à la décision actée dès le début de ce
travail (validation « Learning System multi-stratégies ») : « avant Phase
22, Learning Manager utilise une politique statique/simple ; à partir de
MetaLearner, le choix devient progressivement appris. » Cette étape
implémente EXACTEMENT cette politique statique, maintenant portée par la
classe `MetaLearner` elle-même — pas encore l'apprentissage réel.

**Pourquoi pas encore d'apprentissage réel** (directive : « ne pas
l'implémenter avant que les données nécessaires existent ») : apprendre
« quelle stratégie fonctionne » exigerait de savoir, pour chaque `EvalRun`
passé, QUEL `GenerationMode` a produit sa `Task` — ce lien n'est persisté
nulle part (`EvalRun.method` est `EvalMethod`, un vocabulaire différent de
`GenerationMode`, `docs/COGNITIVE_ARCHITECTURE.md` §5 — jamais fusionnés).
L'ajouter exigerait de modifier le contrat déjà validé d'`EvalRun` (Phase
5) : hors périmètre de cette étape. Tant que ce lien n'existe pas,
`MetaLearner` ne PEUT PAS apprendre empiriquement quelle stratégie a
réussi — une politique apprise construite malgré cette absence serait une
fausse précision, exactement ce que la directive interdit.

**Exploration/exploitation** : appartient conceptuellement à `MetaLearner`
(pas un composant séparé), mais n'est PAS implémentée ici pour la même
raison — aucune donnée historique de performance par stratégie à arbitrer.
"""

from __future__ import annotations

from apex.curriculum.strategy import CurriculumStrategy
from apex.metalearner.model import StrategyDecision
from apex.taskgen.model import GenerationMode
from apex.weakness.model import WeaknessCategory, WeaknessReport


class MetaLearner:
    def __init__(self, *, curriculum: CurriculumStrategy | None = None) -> None:
        self._curriculum = curriculum or CurriculumStrategy()

    def select_strategy(
        self, *, specialty: str, weakness_report: WeaknessReport, current_level: int,
    ) -> StrategyDecision:
        """Compose `CurriculumStrategy.decide()` (Phase 12, réel) pour le
        niveau, puis choisit un `GenerationMode` (Phase 15, réel) — jamais
        un second calcul de progression, jamais un troisième vocabulaire
        de stratégie."""
        curriculum_decision = self._curriculum.decide(
            specialty=specialty, current_level=current_level, weakness_report=weakness_report,
        )
        category = weakness_report.category

        if category is WeaknessCategory.INSUFFICIENT_DATA:
            mode = None
            reason = "données insuffisantes — aucune stratégie recommandée tant qu'aucun signal n'existe"
        elif category is WeaknessCategory.NO_WEAKNESS:
            mode = GenerationMode.CURRICULUM_INTERMEDIATE
            reason = f"aucune faiblesse détectée — progression via curriculum vers le niveau {curriculum_decision.recommended_level}"
        else:
            # ISOLATED_FAILURE / REPEATED_WEAKNESS / STATISTICALLY_CREDIBLE_WEAKNESS :
            # TaskGenerator.TARGETED_WEAKNESS sait déjà dimensionner la sévérité
            # (apex.taskgen.deterministic._SEVERITY_TO_LEVEL) — aucun second
            # mapping sévérité -> niveau dupliqué ici.
            mode = GenerationMode.TARGETED_WEAKNESS
            reason = f"faiblesse détectée ({category.value}) — renforcement ciblé recommandé"

        return StrategyDecision(
            specialty=specialty, mode=mode, level=curriculum_decision.recommended_level,
            curriculum_decision=curriculum_decision, reason=reason,
        )
