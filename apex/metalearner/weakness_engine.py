"""`WeaknessEngineService` — agrège les `JudgeLearningSignal` réels
(N+9) en `GauntletWeakness` (passe « Judge Feedback -> Weakness ->
Curriculum -> Adaptive Evolution », §4/§5).

**Formule déterministe, documentée, jamais un score magique** (§4 de la
mission) : chaque champ de `GauntletWeakness` est une fonction PURE des
`JudgeLearningSignal` déjà persistés — recalculé à la demande, jamais une
seconde table mutable (même discipline qu'`apex.weakness.detector.
detect_for_specialty` vis-à-vis d'`eval_runs`, réutilisée ici pour un
second flux de preuve, jamais dupliquée).

**Ne compte JAMAIS une panne de juge/infrastructure comme une preuve de
faiblesse du candidat** (§4/§11) — seuls `SignalCategory.WEAKNESS`/
`CRITICAL_FAILURE` (`WEAKNESS_SIGNAL_CATEGORIES`) entrent dans
l'agrégation ; `SUCCESS`/`FAILURE`/`INCONCLUSIVE` en sont exclus par
construction.

**Corrélation par lignage (§5)** — « suspected inherited weakness »,
JAMAIS une causalité affirmée : calculée en remontant RÉELLEMENT
`parent_member_id` (`apex.metalearner.population_service.PopulationService.
get_lineage`) pour chaque membre affecté ; un ancêtre commun à TOUS les
membres affectés devient `suspected_inherited_from`, sinon `None`."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from apex.metalearner.judge_feedback_model import (
    WEAKNESS_SIGNAL_CATEGORIES,
    GauntletWeakness,
    ImprovementStatus,
    JudgeLearningSignal,
)
from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

_SEVERITY_ORDER = {
    JudgeSeverity.LOW: 0, JudgeSeverity.MEDIUM: 1, JudgeSeverity.HIGH: 2, JudgeSeverity.CRITICAL: 3,
}

#: Fenêtre de « récence » pour `ImprovementStatus` (§10) — un nombre de
#: générations, pas de temps calendaire (une génération n'a pas de durée
#: fixe). Documentée, pas magique : correspond exactement au nombre d'états
#: du vocabulaire (`PERSISTENT`/`RECOVERING`/`RESOLVED`), voir
#: `_improvement_status`.
_RECENCY_WINDOW_GENERATIONS = 2


def _improvement_status(*, last_seen_generation: int, latest_generation: int) -> ImprovementStatus:
    """Règle EXACTE (§10 de la mission) :
    - observée à la DERNIÈRE génération connue -> `PERSISTENT`.
    - absente de la dernière, présente à l'avant-dernière -> `RECOVERING`.
    - absente des deux dernières -> `RESOLVED`.
    Déterministe : uniquement fonction de deux entiers, jamais d'une
    estimation qualitative."""
    gap = latest_generation - last_seen_generation
    if gap <= 0:
        return ImprovementStatus.PERSISTENT
    if gap == 1:
        return ImprovementStatus.RECOVERING
    return ImprovementStatus.RESOLVED


class WeaknessEngineService:
    def __init__(self, session_factory: Any) -> None:
        from apex.metalearner.judge_feedback_service import JudgeFeedbackService

        self._session_factory = session_factory
        self._feedback = JudgeFeedbackService(session_factory)

    # -- lecture brute (§10 « Learning Memory ») ---------------------------------

    def history(
        self, specialty: str, *, dimension: JudgeDimension | None = None, limit: int = 500,
    ) -> tuple[JudgeLearningSignal, ...]:
        return self._feedback.list_by_specialty(
            specialty, dimension=dimension.value if dimension is not None else None, limit=limit,
        )

    # -- agrégation (§4/§5) -------------------------------------------------------

    def aggregate(self, specialty: str) -> tuple[GauntletWeakness, ...]:
        """Une entrée par `(dimension, failure_category)` RÉELLEMENT
        observé — jamais une entrée fabriquée pour une combinaison qui n'a
        aucune preuve. Trié par sévérité décroissante puis fréquence
        décroissante (le plus urgent en premier, déterministe à données
        égales : ordre stable de `JudgeDimension`)."""
        signals = self.history(specialty)
        if not signals:
            return ()
        latest_generation = max(s.generation for s in signals)

        groups: dict[tuple[JudgeDimension, Any], list[JudgeLearningSignal]] = defaultdict(list)
        for signal in signals:
            if signal.category not in WEAKNESS_SIGNAL_CATEGORIES:
                continue
            groups[(signal.dimension, signal.failure_category)].append(signal)

        weaknesses = []
        for (dimension, failure_category), group in groups.items():
            weaknesses.append(
                self._build_weakness(specialty, dimension, failure_category, group, latest_generation),
            )
        weaknesses.sort(key=lambda w: (-_SEVERITY_ORDER[w.severity], -w.frequency, w.dimension.value))
        return tuple(weaknesses)

    def _build_weakness(
        self,
        specialty: str,
        dimension: JudgeDimension,
        failure_category: Any,
        group: list[JudgeLearningSignal],
        latest_generation: int,
    ) -> GauntletWeakness:
        severity = max(
            (s.severity for s in group if s.severity is not None),
            key=lambda s: _SEVERITY_ORDER[s], default=JudgeSeverity.LOW,
        )
        affected_candidates = tuple(sorted({s.member_id for s in group}))
        affected_generations = tuple(sorted({s.generation for s in group}))
        affected_genomes = tuple(sorted({s.genome_version_id for s in group}))
        last_seen_generation = max(affected_generations)
        first_seen = min(s.created_at for s in group)
        last_seen = max(s.created_at for s in group)
        # Récurrence = nombre de GÉNÉRATIONS DISTINCTES où cette faiblesse
        # est apparue (§5 : « génération 4/5/6 -> récurrent ») — jamais le
        # nombre brut de signaux, qui confondrait plusieurs candidats de la
        # MÊME génération avec une vraie récurrence temporelle.
        recurrence_count = len(affected_generations)
        # Confiance déterministe : fraction de l'échantillon RÉELLEMENT
        # observé, plafonnée à 1.0 — jamais une estimation qualitative.
        # `_CONFIDENCE_SATURATION_COUNT` documente le seuil au-delà duquel
        # la confiance est maximale (même esprit que les seuils déjà
        # documentés d'`apex.weakness.detector`, jamais choisi pour
        # produire un résultat souhaité à l'avance).
        confidence = min(1.0, len(group) / _CONFIDENCE_SATURATION_COUNT)

        return GauntletWeakness(
            specialty=specialty, dimension=dimension, failure_category=failure_category, severity=severity,
            frequency=len(group), confidence=confidence, first_seen=first_seen, last_seen=last_seen,
            first_seen_generation=min(affected_generations), last_seen_generation=last_seen_generation,
            affected_candidates=affected_candidates, affected_generations=affected_generations,
            affected_genomes=affected_genomes, evidence_count=len(group), recurrence_count=recurrence_count,
            improvement_status=_improvement_status(
                last_seen_generation=last_seen_generation, latest_generation=latest_generation,
            ),
            suspected_inherited_from=self._suspected_common_ancestor(affected_candidates),
        )

    def _suspected_common_ancestor(self, member_ids: tuple[str, ...]) -> str | None:
        """§5 : « suspected inherited weakness » — un ancêtre commun RÉEL
        (`parent_member_id`, jamais du contenu de Genome deviné) à TOUS les
        membres affectés. Un seul membre affecté -> jamais de corrélation
        de lignage possible (`None`, honnête : rien à corréler)."""
        if len(member_ids) < 2:
            return None
        from apex.metalearner.population_service import PopulationService

        population = PopulationService(self._session_factory)
        ancestor_chains = []
        for member_id in member_ids:
            lineage = population.get_lineage(member_id)
            ancestor_chains.append([m.id for m in lineage])  # membre lui-même en premier, racine en dernier
        common = set(ancestor_chains[0])
        for chain in ancestor_chains[1:]:
            common &= set(chain)
        if not common:
            return None
        # Le PLUS PROCHE ancêtre commun (le premier, dans l'ordre du
        # PREMIER lignage, qui appartient à l'intersection) — jamais la
        # racine la plus lointaine, moins informative.
        for candidate in ancestor_chains[0]:
            if candidate in common:
                return candidate
        return None


#: Nombre de signaux au-delà duquel la confiance d'une `GauntletWeakness`
#: sature à 1.0 — documenté, arbitraire mais conservateur (même discipline
#: que `apex.weakness.detector`'s propres seuils), jamais ajusté pour
#: produire un résultat souhaité à l'avance.
_CONFIDENCE_SATURATION_COUNT = 5
