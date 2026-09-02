"""`aggregate_model_performance` — agrégat PUR sur des `AgentExecution`
réels pour un `model_id` donné, passe « Adaptive Model Router V1 ».

Même patron que `apex.costgain.estimator.CostGainEstimator` : une fonction
pure qui prend une séquence déjà récupérée par l'appelant (typiquement
`AgentExecutionRepository.list_by_model`), ne touche jamais la DB ni le
réseau elle-même, et ne décide jamais elle-même quel modèle interroger.

**Placement délibéré dans `apex.agent_registry`, PAS `apex.models`/
`apex.routing`** : le graphe d'imports réel du dépôt (vérifié) montre que
`apex.agent_registry` importe déjà `apex.models`/`apex.routing`, jamais
l'inverse. Mettre cette agrégation dans `apex.models` créerait une
dépendance circulaire. `ModelPerformanceStats` (le type de sortie) est
donc défini dans `apex.routing.model`, neutre, et cette fonction est la
SEULE à savoir le construire depuis un historique réel d'exécutions.

**Distinction stricte 0 / aucune donnée / unavailable** (voir docstring de
`ModelPerformanceStats`) : `usage_count=0` sur une séquence vide est un
FAIT réel (« aucune exécution connue »), jamais confondu avec
`success_rate`/`mean_latency_s` en `Measurement.unknown()` (rien à
diviser/moyenner). Aucune valeur n'est jamais devinée pour combler une
absence.
"""

from __future__ import annotations

from collections.abc import Sequence

from apex.agent_registry.model import AgentExecution, AgentExecutionStatus
from apex.obs.status import Measurement
from apex.routing.model import ModelPerformanceStats


def aggregate_model_performance(model_id: str, executions: Sequence[AgentExecution]) -> ModelPerformanceStats:
    """`executions` est typiquement déjà filtrée par `model_id` par
    l'appelant (`AgentExecutionRepository.list_by_model`) — cette fonction
    ne refiltre pas elle-même (elle resterait correcte si on lui passait un
    mélange, mais ce n'est pas son rôle de le détecter : elle compte ce
    qu'on lui donne, rien de plus).

    Cas vide (`executions == ()`) explicitement supporté : `usage_count=0`,
    `success_rate`/`mean_latency_s` restent `Measurement.unknown()` —
    jamais une division par zéro, jamais un `0.0` inventé à la place d'une
    mesure absente.
    """
    usage_count = len(executions)
    success_count = sum(1 for e in executions if e.status is AgentExecutionStatus.COMPLETED)
    failure_count = sum(1 for e in executions if e.status is AgentExecutionStatus.FAILED)

    if usage_count > 0:
        success_rate = Measurement.real(success_count / usage_count)
    else:
        success_rate = Measurement.unknown()

    latencies = [e.elapsed_s for e in executions if e.elapsed_s is not None]
    if latencies:
        mean_latency_s = Measurement.real(sum(latencies) / len(latencies))
    else:
        mean_latency_s = Measurement.unknown()

    prompt_tokens = [e.tokens_prompt for e in executions if e.tokens_prompt is not None]
    mean_tokens_prompt = Measurement.real(sum(prompt_tokens) / len(prompt_tokens)) if prompt_tokens else Measurement.unknown()

    completion_tokens = [e.tokens_completion for e in executions if e.tokens_completion is not None]
    mean_tokens_completion = (
        Measurement.real(sum(completion_tokens) / len(completion_tokens)) if completion_tokens else Measurement.unknown()
    )

    failure_reasons: dict[str, int] = {}
    for execution in executions:
        if execution.status is not AgentExecutionStatus.FAILED or execution.fallback_reason is None:
            continue
        key = execution.fallback_reason.value
        failure_reasons[key] = failure_reasons.get(key, 0) + 1

    return ModelPerformanceStats(
        model_id=model_id,
        usage_count=usage_count,
        success_count=success_count,
        failure_count=failure_count,
        success_rate=success_rate,
        mean_latency_s=mean_latency_s,
        mean_tokens_prompt=mean_tokens_prompt,
        mean_tokens_completion=mean_tokens_completion,
        failure_reasons=failure_reasons,
    )
