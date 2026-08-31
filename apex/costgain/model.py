"""Vocabulaire de `CostGainEstimator` — `ResourceCost`, `CostGainEstimate`.

Registre : `CostGainEstimator`, « gain attendu / coût de calcul, puis
calibration sur le réel » (`phase_defined` 5, `phase_implemented` 18).

**Aucun troisième vocabulaire d'état inventé** : les mesures de coût
réutilisent `apex.obs.status.Measurement`/`DataStatus` (REAL/SIMULATED/
UNKNOWN), déjà le seul vocabulaire reconnu pour « cette donnée est-elle
réellement mesurée ? » dans tout APEX (Gate, `ModelProvider`, `Oracle...`).
Le GAIN réutilise `apex.stattest.StatTestResult` tel quel (Phase 16) : son
propre vocabulaire (`INSUFFICIENT_DATA`/`NOT_SIGNIFICANT`/`SIGNIFICANT`)
porte déjà la distinction « je ne sais pas » / « probablement du bruit » /
« différence réelle » — jamais une mesure fabriquée faute de données.
"""

from __future__ import annotations

from dataclasses import dataclass

from apex.obs.status import Measurement
from apex.stattest.model import StatTestResult


@dataclass(frozen=True)
class ResourceCost:
    """Coûts réellement mesurables aujourd'hui, honnêtement `UNKNOWN` sinon
    — voir `apex.costgain.estimator` pour ce qui est réellement disponible
    et pourquoi (`cpu_time_s`/`memory_peak_mb` ne sont pas encore persistés
    sur `EvalRun`, `token_count` exige un raccordement `ModelProvider`
    inexistant)."""

    elapsed_s: Measurement
    cpu_time_s: Measurement
    memory_peak_mb: Measurement
    token_count: Measurement
    #: Nombre de `EvalRun` considérés — toujours un compte réel, jamais
    #: `UNKNOWN` (un simple `len()`, aucune mesure externe requise).
    experiment_count: int


@dataclass(frozen=True)
class CostGainEstimate:
    """`ratio` (gain/coût) n'est `Measurement.real` QUE si `gain.verdict`
    est `SIGNIFICANT` — un ratio construit sur un gain `NOT_SIGNIFICANT`
    (bruit) ou `INSUFFICIENT_DATA` serait une fausse précision, jamais
    calculé dans ces deux cas."""

    specialty: str
    cost: ResourceCost
    gain: StatTestResult
    ratio: Measurement
    reason: str
