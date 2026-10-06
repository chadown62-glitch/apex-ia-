"""Règle de stratégie fondée sur l'expérience — noyau RUNTIME (06/10/2026).

Extraite telle quelle de `apex.metalearner.adaptive_solution_loop` (la boucle de recherche « G0 -> G1 -> ... »,
qui l'importe désormais d'ici) et `CandidateStrategy` de `apex.metalearner.generated_solution_cycle` : le TaskLoop
du produit en dépend (`TaskRequest.condition_on_run`, bras B du protocole v2). Une seule règle, jamais deux —
voir la docstring de `apex.metalearner.adaptive_solution_loop` pour sa justification complète.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from apex.experience.failure_taxonomy import FailureCategory
from apex.experience.store import Experience, ExperienceRepository, SuccessEvidence
from apex.genome.clone_config import resolve_generation_temperature


@dataclass(frozen=True)
class CandidateStrategy:
    """Une variation RÉELLE de stratégie de génération — `label` est
    purement descriptif (jamais consulté par la logique), `genome_content`
    est le contenu de Genome RÉELLEMENT résolu par `apex.genome.clone_config`
    (`None` -> comportement par défaut du provider, aucune influence)."""

    label: str
    genome_content: dict[str, Any] | None = None


#: Repli neutre quand la stratégie gagnante d'une génération ne portait
#: aucune température explicite (`genome_content is None`, comportement par
#: défaut du provider) — JAMAIS une valeur mesurée, uniquement un point de
#: départ documenté pour calculer la mutation suivante.
_DEFAULT_BASE_TEMPERATURE = 0.7

_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 2.0

#: Catégories où le code a RÉELLEMENT tourné mais produit une réponse
#: fonctionnellement fausse — la seule preuve qui justifie d'ÉLARGIR
#: l'exploration (température augmentée) et de rappeler les cas limites.
_INCORRECT_SOLUTION_HINT = (
    "Vérifie soigneusement les cas limites (chaîne vide, un seul caractère) "
    "et la sensibilité à la casse avant de répondre."
)
#: Catégorie où le candidat n'a jamais pu s'exécuter jusqu'au bout — la
#: preuve qui justifie de RESSERRER (température fortement réduite) et de
#: rappeler strictement le format de réponse attendu.
_INVALID_CANDIDATE_HINT = (
    "Réponds UNIQUEMENT avec du code Python valide et exécutable, sans "
    "explication, sans bloc markdown, sans appel d'exemple."
)

#: Catégories d'échec D'INFRASTRUCTURE (jamais imputables au contenu généré
#: par le candidat) — voir la docstring du module : jamais de mutation de
#: stratégie sur cette seule preuve, seulement une nouvelle tentative.
_INFRASTRUCTURE_CATEGORIES = frozenset(
    {
        FailureCategory.GENERATION_FAILURE,
        FailureCategory.INFRASTRUCTURE_FAILURE,
        FailureCategory.TIMEOUT,
        FailureCategory.RESOURCE_FAILURE,
        FailureCategory.POLICY_SECURITY_REJECTION,
        FailureCategory.TEST_ORACLE_FAILURE,
        FailureCategory.EXECUTION_FAILURE,
    }
)


def _clamp_temperature(value: float) -> float:
    return max(_MIN_TEMPERATURE, min(_MAX_TEMPERATURE, value))


def _base_temperature(generation_config: dict[str, Any]) -> float:
    resolved = resolve_generation_temperature(generation_config)
    return resolved if resolved is not None else _DEFAULT_BASE_TEMPERATURE


def _mutate_from_experience(best: Experience) -> tuple[float, str | None, str]:
    """Retourne `(nouvelle_température, nouveau_suffixe_de_prompt,
    description)` à partir de la SEULE `Experience` gagnante de la
    génération précédente — une fonction PURE, aucun accès DB ici (l'accès
    DB est fait par `decide_next_strategies`, qui appelle celle-ci)."""
    base_temp = _base_temperature(best.generation_config)
    carried_suffix = (best.generation_config.get("generation") or {}).get("prompt_suffix")
    carried_suffix = carried_suffix if isinstance(carried_suffix, str) else None

    if best.success_evidence is SuccessEvidence.CONFIRMED:
        return (
            _clamp_temperature(base_temp * 0.5),
            carried_suffix,
            "génération précédente : SUCCÈS CONFIRMÉ par ré-exécution — température réduite pour "
            "stabiliser une stratégie déjà gagnante (exploitation, pas exploration)",
        )
    if best.success_evidence is SuccessEvidence.OBSERVED:
        return (
            base_temp, carried_suffix,
            "génération précédente : succès OBSERVÉ une seule fois, non confirmé — aucune "
            "exploitation, stratégie inchangée, nouvelle tentative",
        )
    if best.success_evidence is SuccessEvidence.REFUTED:
        return (
            _clamp_temperature(base_temp + 0.4),
            _INCORRECT_SOLUTION_HINT,
            "génération précédente : succès RÉFUTÉ (le même code a donné un autre verdict à la "
            "ré-exécution) — traité comme une réponse incorrecte : exploration élargie",
        )
    if best.failure_category is FailureCategory.INCORRECT_SOLUTION:
        return (
            _clamp_temperature(base_temp + 0.4),
            _INCORRECT_SOLUTION_HINT,
            "génération précédente : réponse incorrecte mais le code s'est réellement "
            "exécuté — exploration élargie (température augmentée) et rappel des cas limites",
        )
    if best.failure_category is FailureCategory.INVALID_CANDIDATE:
        return (
            _clamp_temperature(base_temp * 0.3),
            _INVALID_CANDIDATE_HINT,
            "génération précédente : candidat invalide (le script n'a jamais produit son "
            "résultat) — température fortement réduite et rappel strict du format attendu",
        )
    # `_INFRASTRUCTURE_CATEGORIES` : voir la docstring du module — jamais de
    # mutation sur cette seule preuve, une nouvelle tentative à l'identique.
    return (
        base_temp, carried_suffix,
        f"génération précédente : échec d'infrastructure ({best.failure_category.value}), "
        "non imputable au contenu généré — stratégie inchangée, nouvelle tentative",
    )


def _spread_strategies(
    *, generation_number: int, base_temperature: float, prompt_suffix: str | None, count: int
) -> tuple[CandidateStrategy, ...]:
    """Répartit `count` candidats RÉELS autour de `base_temperature` — un pas
    de 0.1 par candidat, centré, borné à `[0.0, 2.0]` — jamais des candidats
    strictement identiques (la comparaison multi-candidats N+19 resterait
    sinon triviale), mais tous dérivés de la MÊME décision, evidence-based."""
    if count == 1:
        offsets = [0.0]
    else:
        mid = (count - 1) / 2
        offsets = [(i - mid) * 0.1 for i in range(count)]
    strategies = []
    for i, offset in enumerate(offsets):
        temperature = _clamp_temperature(base_temperature + offset)
        generation: dict[str, Any] = {"temperature": temperature}
        if prompt_suffix is not None:
            generation["prompt_suffix"] = prompt_suffix
        strategies.append(
            CandidateStrategy(
                label=f"g{generation_number}-mut{i}", genome_content={"generation": generation},
            )
        )
    return tuple(strategies)


@dataclass(frozen=True)
class StrategyDecision:
    """Décision de stratégie ET sa provenance : `source` est l'`Experience` RÉELLE dont elle dérive
    (`None` -> aucune expérience, stratégies de repli inchangées)."""

    strategies: tuple[CandidateStrategy, ...]
    description: str
    source: Experience | None


def decide_strategy_from_experiences(
    prior_experiences: Sequence[Experience], *, base_strategies: Sequence[CandidateStrategy]
) -> StrategyDecision:
    """La RÈGLE de décision, pure (aucun accès DB) — partagée par `decide_next_strategies` (générations de
    `apex solve`) et par le TaskLoop (`TaskRequest.condition_on_run`, audit du 04/10/2026) : une seule règle,
    jamais deux. Sans expérience : `base_strategies` INCHANGÉES (jamais une amélioration fabriquée)."""
    if not prior_experiences:
        return StrategyDecision(
            strategies=tuple(base_strategies),
            description=(
                "aucune expérience trouvée pour la génération précédente — "
                "stratégie de repli inchangée (jamais une amélioration fabriquée)"
            ),
            source=None,
        )
    # Référence : un succès confirmé avant tout, un succès réfuté en dernier, puis la fitness.
    best = max(
        prior_experiences,
        key=lambda e: (e.confirmed_success, e.success_evidence is not SuccessEvidence.REFUTED, e.fitness),
    )
    new_temperature, new_suffix, description = _mutate_from_experience(best)
    strategies = _spread_strategies(
        generation_number=best.generation_number + 1, base_temperature=new_temperature,
        prompt_suffix=new_suffix, count=len(base_strategies),
    )
    return StrategyDecision(strategies=strategies, description=description, source=best)


def decide_next_strategies(
    experience_repo: ExperienceRepository,
    *,
    task_id: str,
    prior_generation_number: int,
    base_strategies: Sequence[CandidateStrategy],
    experiment_ref: str | None = None,
) -> tuple[tuple[CandidateStrategy, ...], str]:
    """Lit les `Experience` RÉELLEMENT persistées de la génération
    `prior_generation_number` (requête DB réelle, `list_by_task_and_generation`)
    et en dérive la stratégie de la génération suivante. **Propriété
    vérifiée par test** : si ces lignes ont été supprimées (ou n'ont jamais
    existé), cette fonction retombe sur `base_strategies` INCHANGÉES — la
    décision dépend RÉELLEMENT de l'expérience persistée, jamais d'un état
    en mémoire par ailleurs."""
    prior_experiences = experience_repo.list_by_task_and_generation(
        task_id, prior_generation_number, experiment_ref=experiment_ref,
    )
    decision = decide_strategy_from_experiences(prior_experiences, base_strategies=base_strategies)
    return decision.strategies, decision.description
