"""`run_adaptive_learning_loop` — boucle G0 -> G1 -> G2 -> ... de la mission
N+20 (« Generated-Solution Evaluation Loop » devient une VRAIE boucle
d'apprentissage) : compose `apex.metalearner.generated_solution_cycle.
compare_generated_solutions` (réutilisé, N+19) génération après génération,
en dérivant la stratégie de CHAQUE génération de l'ANALYSE RÉELLE des
`Experience` PERSISTÉES de la génération précédente
(`apex.metalearner.experience`, N+20) — jamais une génération suivante
indépendante (§ mission : « si supprimer l'expérience de G0 ne change
absolument rien à la décision de G1, ce n'est pas une vraie boucle
d'apprentissage » — voir `tests/test_adaptive_solution_loop.py`, qui
vérifie exactement cette propriété en supprimant les lignes `experiences`
d'une génération et en observant que la génération suivante retombe alors
sur sa stratégie de repli, jamais la même décision informée).

Aucune nouvelle abstraction de routing/fitness/sandbox : ce module compose
`EvalRunnerService`/`AgentRuntime`/`compute_fitness`/`classify_failure`/
`compare_generated_solutions`, tous déjà réels — la seule pièce nouvelle est
la RÈGLE DE DÉCISION (`decide_next_strategies`), une fonction PURE qui lit
des preuves déjà persistées (jamais un second système d'évaluation).

**Discipline anti-fabrication (§ mission)** : cette règle ne conclut JAMAIS
« fitness mauvais = stratégie mauvaise ». Une `FailureCategory` d'origine
INFRASTRUCTURE (génération/sandbox/ressources/timeout/politique — voir
`apex.metalearner.failure_taxonomy`) laisse la stratégie INCHANGÉE (une
nouvelle tentative, pas une mutation) : muter une stratégie à cause d'un
échec d'infrastructure inventerait une leçon à partir de bruit. Seules
`INCORRECT_SOLUTION` (le code a tourné, la réponse est fausse) et
`INVALID_CANDIDATE` (le code n'a pas pu tourner du tout) justifient une
mutation réelle de la température/du suffixe de prompt — les deux seuls
leviers de génération câblés au Genome à ce jour
(`apex.genome.clone_config.resolve_generation_temperature`/
`resolve_generation_prompt_suffix`)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool, ModelEndpoint
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.genome.clone_config import resolve_generation_temperature
from apex.metalearner.experience import Experience, ExperienceRepository, build_experience
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.generated_solution_cycle import CandidateComparison, CandidateOutcome, CandidateStrategy, compare_generated_solutions
from apex.models.provider import ModelProvider
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits

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

    if best.failure_category is FailureCategory.SUCCESS:
        return (
            _clamp_temperature(base_temp * 0.5),
            carried_suffix,
            "génération précédente : SUCCÈS — température réduite pour stabiliser "
            "une stratégie déjà gagnante (exploitation, pas exploration)",
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
    if not prior_experiences:
        return (
            tuple(base_strategies),
            "aucune expérience trouvée pour la génération précédente — "
            "stratégie de repli inchangée (jamais une amélioration fabriquée)",
        )

    best = max(prior_experiences, key=lambda experience: experience.fitness)
    new_temperature, new_suffix, description = _mutate_from_experience(best)
    strategies = _spread_strategies(
        generation_number=prior_generation_number + 1, base_temperature=new_temperature,
        prompt_suffix=new_suffix, count=len(base_strategies),
    )
    return strategies, description


@dataclass(frozen=True)
class AdaptiveLoopConfig:
    """Limites explicites — § mission N+20 : « jamais de boucle autonome
    infinie ». `max_generations` est TOUJOURS actif (jamais `None`) ; les
    trois autres critères sont optionnels et s'ajoutent, jamais ne
    remplacent, cette borne absolue."""

    max_generations: int = 5
    target_fitness: float | None = None
    no_improvement_patience: int | None = 2
    time_budget_s: float | None = None

    def __post_init__(self) -> None:
        if self.max_generations < 1:
            raise ValueError("« max_generations » doit être >= 1")


@dataclass(frozen=True)
class GenerationRecord:
    generation_number: int
    comparison: CandidateComparison
    experiences: tuple[Experience, ...]
    best_experience: Experience
    mutation_description: str


@dataclass(frozen=True)
class AdaptiveLoopResult:
    task_id: str
    experiment_ref: str | None
    generations: tuple[GenerationRecord, ...]
    stop_reason: str
    best_overall: CandidateOutcome
    best_overall_generation: int


def run_adaptive_learning_loop(
    evalrunner: EvalRunnerService,
    agent_runtime: AgentRuntime,
    experience_repo: ExperienceRepository,
    *,
    task_id: str,
    endpoint: ModelEndpoint,
    pool: ComputePool,
    initial_strategies: Sequence[CandidateStrategy],
    config: AdaptiveLoopConfig,
    method: EvalMethod = EvalMethod.EXPERIMENTATION,
    experiment_ref: str | None = None,
    limits: ResourceLimits | None = None,
    provider: ModelProvider | None = None,
    requested_by: Subject = Subject.CORE,
    clone_subject: Subject = Subject.RESEARCH,
) -> AdaptiveLoopResult:
    """G0 -> G1 -> ... : à chaque génération > 0, la stratégie est RÉ-DÉRIVÉE
    de l'expérience RÉELLEMENT persistée de la génération précédente
    (`decide_next_strategies`, lecture DB réelle) — jamais une simple
    répétition. Chaque candidat produit une `Experience` réellement
    persistée AVANT que la décision de la génération suivante ne soit
    prise (ordre séquentiel strict, jamais parallèle entre générations)."""
    if not initial_strategies:
        raise ValueError("« initial_strategies » ne peut pas être vide")

    # Sans identifiant propre, la G1 lirait la meilleure G0 de TOUTES les exécutions de la tâche.
    if experiment_ref is None:
        experiment_ref = f"adaptive-loop:{uuid.uuid4()}"

    start = time.monotonic()
    strategies = tuple(initial_strategies)
    generations: list[GenerationRecord] = []
    best_overall: CandidateOutcome | None = None
    best_overall_generation = -1
    running_best_fitness = float("-inf")
    generations_without_improvement = 0
    stop_reason = "max_generations_reached"

    for generation_number in range(config.max_generations):
        if generation_number == 0:
            mutation_description = (
                "génération initiale — stratégies fournies par l'appelant, "
                "aucune expérience préalable"
            )
        else:
            strategies, mutation_description = decide_next_strategies(
                experience_repo, task_id=task_id, prior_generation_number=generation_number - 1,
                base_strategies=strategies, experiment_ref=experiment_ref,
            )

        comparison = compare_generated_solutions(
            evalrunner, agent_runtime, task_id=task_id, endpoint=endpoint, strategies=strategies,
            pool=pool, method=method, experiment_ref=experiment_ref, limits=limits, provider=provider,
            requested_by=requested_by, clone_subject=clone_subject,
        )

        parent = generations[-1].best_experience if generations else None
        # `improvement_delta` (§ mission) reste une mesure DE GÉNÉRATION
        # (fitness de CE candidat - meilleur fitness connu avant cette
        # génération) — la même référence `running_best_fitness` pour tous
        # les candidats de cette génération, jamais un delta entre
        # candidats de la MÊME génération (ce ne serait pas une mesure
        # d'apprentissage inter-génération).
        prior_running_best = running_best_fitness if generations else None

        experiences: list[Experience] = []
        for outcome, strategy in zip(comparison.candidates, strategies, strict=True):
            experience = build_experience(
                outcome.eval_run, outcome.fitness, strategy_id=strategy.label,
                generation_number=generation_number, generation_config=strategy.genome_content or {},
                created_by=requested_by.value,
                parent_experience_id=parent.id if parent else None,
                parent_generation=parent.generation_number if parent else None,
                mutation_description=mutation_description,
                improvement_delta=(
                    outcome.fitness.value - prior_running_best if prior_running_best is not None else None
                ),
            )
            experience_repo.create(experience)
            experiences.append(experience)

        best_experience = max(experiences, key=lambda experience: experience.fitness)
        generations.append(
            GenerationRecord(
                generation_number=generation_number, comparison=comparison, experiences=tuple(experiences),
                best_experience=best_experience, mutation_description=mutation_description,
            )
        )

        if best_overall is None or comparison.best.fitness.value > best_overall.fitness.value:
            best_overall = comparison.best
            best_overall_generation = generation_number

        if best_experience.fitness > running_best_fitness:
            generations_without_improvement = 0
        else:
            generations_without_improvement += 1
        running_best_fitness = max(running_best_fitness, best_experience.fitness)

        if config.target_fitness is not None and best_experience.fitness >= config.target_fitness:
            stop_reason = "target_fitness_reached"
            break
        if generation_number + 1 >= config.max_generations:
            stop_reason = "max_generations_reached"
            break
        if (
            config.no_improvement_patience is not None
            and generations_without_improvement >= config.no_improvement_patience
        ):
            stop_reason = "no_improvement_patience_exhausted"
            break
        if config.time_budget_s is not None and (time.monotonic() - start) >= config.time_budget_s:
            stop_reason = "time_budget_exhausted"
            break

    assert best_overall is not None  # au moins une génération a toujours tourné (boucle for >= 1 tour)
    return AdaptiveLoopResult(
        task_id=task_id, experiment_ref=experiment_ref, generations=tuple(generations),
        stop_reason=stop_reason, best_overall=best_overall, best_overall_generation=best_overall_generation,
    )
