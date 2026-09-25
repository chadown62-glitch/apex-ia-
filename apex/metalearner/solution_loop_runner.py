"""Point d'entrée réel de la boucle `run_adaptive_learning_loop` (avant :
aucun appelant hors tests — boucle dormante, constaté par le Continuous
Improvement Engine).

Compose UNIQUEMENT des services existants : `AgentRuntime`,
`EvalRunnerService`, `ExperienceRepository`, la résolution rôle -> endpoint
de `apex.models.registry`, et le pool local ACTIVE de la configuration.
Aucune logique d'évaluation ni d'apprentissage n'est ajoutée ici."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex.config.schema import ApexConfig, ComputePool, ModelEndpoint, PoolLocation, PoolStatus
from apex.metalearner.adaptive_solution_loop import (
    AdaptiveLoopConfig,
    AdaptiveLoopResult,
    run_adaptive_learning_loop,
)
from apex.metalearner.generated_solution_cycle import CandidateStrategy
from apex.models.provider import ModelProvider
from apex.models.registry import filter_by_role, resolve_candidates


class SolutionLoopUnavailable(RuntimeError):
    """Précondition manquante (aucun endpoint pour le rôle, aucun pool
    local ACTIVE) — jamais contournée par une valeur devinée."""


@dataclass(frozen=True)
class SolutionLoopRequest:
    task_id: str
    role: str = "coder_main"
    generations: int = 3
    candidates: int = 2
    base_temperature: float = 0.7
    target_fitness: float | None = 1.0
    experiment_ref: str | None = None


def resolve_endpoint(config: ApexConfig, role: str) -> ModelEndpoint:
    """Premier candidat du rôle portant un endpoint déclaré — l'ordre des
    candidats est celui de `config.yaml` (le candidat de référence en tête)."""
    for candidate in filter_by_role(resolve_candidates(config), role):
        if candidate.endpoint is not None:
            return candidate.endpoint
    raise SolutionLoopUnavailable(f"aucun endpoint déclaré pour le rôle « {role} »")


def resolve_local_pool(config: ApexConfig) -> ComputePool:
    for pool in config.pools.values():
        if pool.location is PoolLocation.LOCAL and pool.status is PoolStatus.ACTIVE:
            return pool
    raise SolutionLoopUnavailable("aucun pool de calcul local ACTIVE dans la configuration")


def initial_strategies(base_temperature: float, count: int) -> tuple[CandidateStrategy, ...]:
    if count < 1:
        raise ValueError("« candidates » doit être >= 1")
    mid = (count - 1) / 2
    return tuple(
        CandidateStrategy(
            label=f"g0-mut{i}",
            genome_content={
                "generation": {"temperature": max(0.0, min(2.0, base_temperature + (i - mid) * 0.1))}
            },
        )
        for i in range(count)
    )


def run_solution_loop(
    session_factory: Any,
    config: ApexConfig,
    request: SolutionLoopRequest,
    *,
    provider: ModelProvider | None = None,
) -> AdaptiveLoopResult:
    from apex.agents.runtime import AgentRuntime
    from apex.evalrunner.service import EvalRunnerService
    from apex.metalearner.experience import ExperienceRepository

    runtime = AgentRuntime(session_factory)
    return run_adaptive_learning_loop(
        EvalRunnerService(session_factory, agent_runtime=runtime),
        runtime,
        ExperienceRepository(session_factory),
        task_id=request.task_id,
        endpoint=resolve_endpoint(config, request.role),
        pool=resolve_local_pool(config),
        initial_strategies=initial_strategies(request.base_temperature, request.candidates),
        config=AdaptiveLoopConfig(
            max_generations=request.generations, target_fitness=request.target_fitness
        ),
        experiment_ref=request.experiment_ref,
        provider=provider,
    )


def summarize(result: AdaptiveLoopResult) -> dict[str, Any]:
    best = result.best_overall
    return {
        "task_id": result.task_id,
        "experiment_ref": result.experiment_ref,
        "stop_reason": result.stop_reason,
        "best_generation": result.best_overall_generation,
        "best_fitness": best.fitness.value,
        "best_verdict": best.eval_run.oracle_verdict.value,
        "generations": [
            {
                "generation": g.generation_number,
                "best_fitness": g.best_experience.fitness,
                "best_category": g.best_experience.failure_category.value,
                "decision": g.mutation_description,
                "candidates": [
                    {
                        "strategy": c.strategy_label,
                        "verdict": c.eval_run.oracle_verdict.value,
                        "fitness": c.fitness.value,
                        "eval_run_id": c.eval_run.id,
                    }
                    for c in g.comparison.candidates
                ],
            }
            for g in result.generations
        ],
    }
