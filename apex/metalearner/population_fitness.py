"""`compute_fitness` — fonction PURE, déterministe, sur un `EvalRun` déjà
réel (`apex.evalrunner.model.EvalRun`, Phase 5) — jamais une seconde
exécution, jamais une métrique inventée (§7 de la mission : « NE PAS
inventer des métriques qui n'existent pas »).

Composantes RÉELLES utilisées, et pourquoi rien d'autre :
- `quality` (0.0/1.0) : `oracle_verdict is PASS` — le seul signal de
  correction qui existe déjà (`apex.oracles.model.OracleVerdict`).
- `robustness` (0.0/1.0) : l'exécution a RÉELLEMENT abouti sans erreur
  d'infrastructure (`PASS` ou `FAIL`, jamais `ERROR`/`TIMEOUT`/`UNKNOWN`) —
  distinct de `quality` : un `FAIL` est robuste (le sandbox a bien tourné et
  rendu un jugement) mais pas de qualité (le jugement est négatif).
- `cost_penalty` ([0.0, 1.0]) : `elapsed_s` normalisé par le budget
  `timeout_s` réellement appliqué à CETTE exécution (`ResourceLimits.
  timeout_s`, déjà réel depuis la Phase 2) — jamais un temps absolu
  arbitraire. `UNKNOWN` (jamais mesuré, `elapsed_s is None`) -> `0.0`,
  `cost_known=False` exposé séparément dans les composantes : ne JAMAIS
  laisser croire qu'un coût inconnu était nul.
- `error_penalty` (0.0/1.0) : `1.0` si `ERROR`/`TIMEOUT` (échec
  d'infrastructure, indépendant de la difficulté de la tâche), `0.0` sinon.

`latency_penalty` du §7 de la mission N'EST PAS une composante séparée :
`elapsed_s` est la SEULE mesure de temps réellement disponible
(`apex.costgain.estimator` documente déjà pourquoi `cpu_time_s` reste
`UNKNOWN`) — l'exposer sous deux noms différents inventerait une distinction
qui n'existe pas dans les données.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex.evalrunner.model import EvalRun
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ResourceLimits

#: Poids par défaut — documentés, jamais choisis pour produire un résultat
#: souhaité à l'avance (même discipline que `RouterScoringPolicy`,
#: `apex.config.schema`). Non configurables dans cette passe (aucun
#: consommateur réel ne justifie encore de les exposer — ADR-0007).
QUALITY_WEIGHT = 1.0
ROBUSTNESS_WEIGHT = 0.2
COST_WEIGHT = 0.1
ERROR_WEIGHT = 0.3

_ROBUST_VERDICTS = frozenset({OracleVerdict.PASS, OracleVerdict.FAIL})
_ERROR_VERDICTS = frozenset({OracleVerdict.ERROR, OracleVerdict.TIMEOUT})


@dataclass(frozen=True)
class FitnessResult:
    value: float
    components: dict[str, Any]


def compute_fitness(eval_run: EvalRun, *, limits: ResourceLimits) -> FitnessResult:
    """`limits` doit être celles RÉELLEMENT appliquées à `eval_run` (celles
    résolues par `apex.genome.clone_config.resolve_resource_limits` pour ce
    membre précis) — jamais une valeur de config globale qui ignorerait un
    override de Genome, ce qui fausserait `cost_penalty`."""
    quality = 1.0 if eval_run.oracle_verdict is OracleVerdict.PASS else 0.0
    robustness = 1.0 if eval_run.oracle_verdict in _ROBUST_VERDICTS else 0.0
    error_penalty = 1.0 if eval_run.oracle_verdict in _ERROR_VERDICTS else 0.0

    cost_known = eval_run.elapsed_s is not None
    if cost_known and limits.timeout_s > 0:
        cost_penalty = min(1.0, max(0.0, eval_run.elapsed_s / limits.timeout_s))
    else:
        cost_penalty = 0.0

    value = (
        QUALITY_WEIGHT * quality
        + ROBUSTNESS_WEIGHT * robustness
        - COST_WEIGHT * cost_penalty
        - ERROR_WEIGHT * error_penalty
    )
    return FitnessResult(
        value=value,
        components={
            "quality": quality,
            "robustness": robustness,
            "cost_penalty": cost_penalty,
            "cost_known": cost_known,
            "error_penalty": error_penalty,
            "oracle_verdict": eval_run.oracle_verdict.value,
        },
    )
