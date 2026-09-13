"""`compare_generated_solutions` — boucle MULTI-CANDIDATS de la passe
« Generated-Solution Evaluation Loop » (N+19) : compose
`EvalRunnerService.run_generated_solution` sur PLUSIEURS stratégies
(actuellement : `generation.temperature`, le seul paramètre de génération
câblé au Genome — voir `apex.genome.clone_config.
resolve_generation_temperature`), calcule un fitness RÉEL par candidat
(`apex.metalearner.population_fitness.compute_fitness`, réutilisé tel quel,
JAMAIS un second système de fitness), et retourne le meilleur candidat.

Même patron de composition qu'`apex.metalearner.cycle.run_experiment_cycle` :
une fonction PURE de composition, aucune nouvelle abstraction Router/
Orchestrator. Vit dans `apex.metalearner` (jamais dans `apex.evalrunner`,
qui ne doit jamais importer une couche supérieure — même discipline de
dépendance à sens unique que documentée dans `apex.taskgen.model`)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool, ModelEndpoint
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.service import EvalRunnerService
from apex.metalearner.population_fitness import FitnessResult, compute_fitness
from apex.models.provider import ModelProvider
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits


@dataclass(frozen=True)
class CandidateStrategy:
    """Une variation RÉELLE de stratégie de génération — `label` est
    purement descriptif (jamais consulté par la logique), `genome_content`
    est le contenu de Genome RÉELLEMENT résolu par `apex.genome.clone_config`
    (`None` -> comportement par défaut du provider, aucune influence)."""

    label: str
    genome_content: dict[str, Any] | None = None


@dataclass(frozen=True)
class CandidateOutcome:
    strategy_label: str
    eval_run: EvalRun
    fitness: FitnessResult


@dataclass(frozen=True)
class CandidateComparison:
    """`best` est TOUJOURS le candidat de fitness la plus haute parmi
    `candidates` — jamais un choix arbitraire ; avec au moins un candidat,
    `best` n'est jamais `None` (`compute_fitness` produit une valeur, même
    basse, pour un candidat en erreur — voir sa docstring)."""

    task_id: str
    experiment_ref: str | None
    candidates: tuple[CandidateOutcome, ...]
    best: CandidateOutcome | None


def _resolve_limits(genome_content: dict[str, Any] | None, *, limits: ResourceLimits | None) -> ResourceLimits:
    if limits is not None:
        return limits
    from apex.config.loader import load_config
    from apex.genome import clone_config

    config = load_config()
    base = ResourceLimits.from_config(config.sandbox)
    return clone_config.resolve_resource_limits(genome_content, base)


def compare_generated_solutions(
    evalrunner: EvalRunnerService,
    agent_runtime: AgentRuntime,
    *,
    task_id: str,
    endpoint: ModelEndpoint,
    strategies: Sequence[CandidateStrategy],
    pool: ComputePool,
    method: EvalMethod = EvalMethod.EXPERIMENTATION,
    experiment_ref: str | None = None,
    limits: ResourceLimits | None = None,
    provider: ModelProvider | None = None,
    requested_by: Subject = Subject.CORE,
    clone_subject: Subject = Subject.RESEARCH,
) -> CandidateComparison:
    """Exécute `run_generated_solution` UNE fois par `CandidateStrategy` —
    au moins un élément requis (`strategies` vide -> `ValueError`, jamais une
    comparaison vide silencieusement retournée). Chaque candidat obtient un
    fitness RÉEL (`compute_fitness`, sur son propre `EvalRun` réellement
    persisté) — aucune tentative n'influence une autre : le nombre de
    candidats est entièrement déterminé par `len(strategies)` (§11 :
    « le nombre de candidats doit être configurable »).

    Un `Clone` (`apex.agents.model`) ne peut réellement exécuter qu'UNE
    tentative sandboxée avant de passer à un état terminal (`AgentRuntime.
    execute_in_sandbox` l'exige `READY`) — cette fonction spawn donc un
    NOUVEAU clone réel par candidat, jamais un clone partagé/réexécuté."""
    if not strategies:
        raise ValueError("« strategies » ne peut pas être vide — au moins un candidat est requis")

    outcomes: list[CandidateOutcome] = []
    for strategy in strategies:
        clone = agent_runtime.spawn(
            clone_subject, pool=pool, genome_ref=f"generated-solution:{strategy.label}",
            spawned_by=requested_by,
        )
        run = evalrunner.run_generated_solution(
            task_id=task_id, clone_id=clone.clone_id, endpoint=endpoint, method=method,
            provider=provider, experiment_ref=experiment_ref, limits=limits,
            genome_content=strategy.genome_content, requested_by=requested_by,
        )
        resolved_limits = _resolve_limits(strategy.genome_content, limits=limits)
        fitness = compute_fitness(run, limits=resolved_limits)
        outcomes.append(CandidateOutcome(strategy_label=strategy.label, eval_run=run, fitness=fitness))

    best = max(outcomes, key=lambda outcome: outcome.fitness.value)
    return CandidateComparison(
        task_id=task_id, experiment_ref=experiment_ref, candidates=tuple(outcomes), best=best,
    )

