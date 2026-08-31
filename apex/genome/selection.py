"""`SelectionStrategy` — successive halving, front de Pareto, phase 17.

Fonctions PURES de sélection sur des scores déjà mesurés — n'exécute
JAMAIS rien (ni Clone, ni Sandbox, ni EvalRunner) : les scores viennent
TOUJOURS de l'appelant (typiquement dérivés d'`apex.evalrunner`/
`apex.calibration.ConfidenceCalibrator`, réels depuis les Phases 5/12),
jamais recalculés ici. `Experiment.clones_participants` (population
expérimentale) et cette sélection restent deux préoccupations séparées —
aucune primitive d'exécution nouvelle, aucun `PopulationManager`.

« Successive » (halving) : ce module fournit le primitif D'UN round —
garder la fraction la mieux notée. Enchaîner plusieurs rounds à budget
croissant reste la responsabilité d'un futur orchestrateur
(`ExperimentEngine`, composite documenté, pas une classe séparée) — non
construit ici (pas de raccordement prématuré).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeVar

CandidateId = TypeVar("CandidateId")


def successive_halving(
    scores: Mapping[CandidateId, float], *, keep_fraction: float = 0.5,
) -> tuple[CandidateId, ...]:
    """Retourne les identifiants des `keep_fraction` candidats les mieux
    notés (score le plus élevé = meilleur), au moins 1 survivant si
    `scores` est non vide. Égalités départagées par ordre d'insertion
    (stable) — jamais un ordre arbitraire non reproductible."""
    if not (0.0 < keep_fraction <= 1.0):
        raise ValueError("keep_fraction doit être dans ]0.0, 1.0]")
    if not scores:
        return ()
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    n_keep = max(1, math.ceil(len(ordered) * keep_fraction))
    return tuple(candidate_id for candidate_id, _ in ordered[:n_keep])


def pareto_front(
    objectives: Mapping[CandidateId, Sequence[float]],
) -> frozenset[CandidateId]:
    """Front de Pareto : les candidats qu'AUCUN autre ne domine.

    Convention explicite : chaque objectif est déjà orienté « plus grand
    est meilleur » — un appelant qui minimise un coût doit lui passer
    `-coût` (ou tout signe inversé), ce module ne suppose rien sur le SENS
    de chaque objectif, seulement qu'ils sont tous à MAXIMISER tels
    fournis. `A` domine `B` si `A >= B` sur tous les objectifs ET `A > B`
    sur au moins un — définition standard, pas une variante inventée."""
    ids = list(objectives)
    if not ids:
        return frozenset()
    lengths = {len(objectives[i]) for i in ids}
    if len(lengths) > 1:
        raise ValueError("tous les candidats doivent avoir le même nombre d'objectifs")

    def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
        return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))

    front = set()
    for candidate in ids:
        dominated = any(
            other != candidate and dominates(objectives[other], objectives[candidate]) for other in ids
        )
        if not dominated:
            front.add(candidate)
    return frozenset(front)
