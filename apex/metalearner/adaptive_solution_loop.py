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
from apex.metalearner.experience import Experience, ExperienceRepository, SuccessEvidence, build_experience
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.generated_solution_cycle import (
    CandidateComparison,
    CandidateOutcome,
    CandidateStrategy,
    ChampionConfirmation,
    compare_generated_solutions,
    confirm_by_reexecution,
)
from apex.models.provider import ModelProvider
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits

from apex.experience.strategy import (  # noqa: F401 — règle déplacée dans le noyau runtime
    _DEFAULT_BASE_TEMPERATURE,
    _MIN_TEMPERATURE,
    _MAX_TEMPERATURE,
    _INCORRECT_SOLUTION_HINT,
    _INVALID_CANDIDATE_HINT,
    _INFRASTRUCTURE_CATEGORIES,
    _clamp_temperature,
    _base_temperature,
    _mutate_from_experience,
    _spread_strategies,
    StrategyDecision,
    decide_strategy_from_experiences,
    decide_next_strategies,
)


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
    #: Ré-exécutions du MÊME code avant de croire un champion qui a réussi
    #: (`RobustnessJudge`) ; 0 désactive explicitement la confirmation.
    confirmation_runs: int = 2

    def __post_init__(self) -> None:
        if self.max_generations < 1:
            raise ValueError("« max_generations » doit être >= 1")
        if self.confirmation_runs < 0:
            raise ValueError("« confirmation_runs » doit être >= 0")


@dataclass(frozen=True)
class GenerationRecord:
    generation_number: int
    comparison: CandidateComparison
    experiences: tuple[Experience, ...]
    best_experience: Experience
    mutation_description: str
    champion_confirmation: ChampionConfirmation | None = None


@dataclass(frozen=True)
class AdaptiveLoopResult:
    task_id: str
    experiment_ref: str | None
    generations: tuple[GenerationRecord, ...]
    stop_reason: str
    best_overall: CandidateOutcome
    best_overall_generation: int
    best_overall_confirmed: bool | None = None


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
    best_overall_score = float("-inf")
    best_overall_confirmed: bool | None = None
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

        champion = comparison.best
        assert champion is not None  # `compare_generated_solutions` exige au moins un candidat
        champion_index = comparison.candidates.index(champion)
        # Confirmer AVANT de persister : l'expérience écrite porte la force réelle de sa preuve.
        confirmation: ChampionConfirmation | None = None
        if config.confirmation_runs > 0 and champion.eval_run.oracle_verdict is OracleVerdict.PASS:
            confirmation = confirm_by_reexecution(
                evalrunner, agent_runtime, task_id=task_id, endpoint=endpoint, pool=pool, outcome=champion,
                strategy=strategies[champion_index], runs=config.confirmation_runs,
                method=method, experiment_ref=experiment_ref, limits=limits, requested_by=requested_by,
                clone_subject=clone_subject,
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
        for index, (outcome, strategy) in enumerate(zip(comparison.candidates, strategies, strict=True)):
            is_confirmed_champion = confirmation is not None and index == champion_index
            evidence = confirmation.success_evidence if is_confirmed_champion else None
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
                success_evidence=evidence,
            )
            experience_repo.create(experience)
            experiences.append(experience)

        best_experience = max(experiences, key=lambda experience: experience.fitness)
        # Un champion non reproductible vaut sa PIRE exécution, jamais sa meilleure.
        score = best_experience.fitness
        if confirmation is not None and not confirmation.confirmed:
            score = min(score, confirmation.worst_fitness)
        generations.append(
            GenerationRecord(
                generation_number=generation_number, comparison=comparison, experiences=tuple(experiences),
                best_experience=best_experience, mutation_description=mutation_description,
                champion_confirmation=confirmation,
            )
        )

        if best_overall is None or score > best_overall_score:
            best_overall = champion
            best_overall_generation = generation_number
            best_overall_score = score
            best_overall_confirmed = None if confirmation is None else confirmation.confirmed

        if score > running_best_fitness:
            generations_without_improvement = 0
        else:
            generations_without_improvement += 1
        running_best_fitness = max(running_best_fitness, score)

        confirmed_or_unneeded = confirmation is None or confirmation.confirmed
        if config.target_fitness is not None and score >= config.target_fitness and confirmed_or_unneeded:
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
        best_overall_confirmed=best_overall_confirmed,
    )
