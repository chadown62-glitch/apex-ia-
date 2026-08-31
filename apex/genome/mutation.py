"""`MutationOperator` — mutation ET crossover/recombinaison, phase 17.

Registre : `MutationOperator`, « opérateurs d'évolution sur le Genome »
(`phase_defined` 4, `phase_implemented` 17). Directive Phase 17 explicite :
« Le crossover doit être explicitement supporté lorsque MutationOperator
sera implémenté » — pas de `RecombinationEngine` séparé, le crossover est
une fonction de ce module, pas une nouvelle abstraction.

Fonctions PURES sur des `dict` JSON-sérialisables ordinaires — jamais
couplées à `apex.genome.service`/`ArtifactStoreProvider`/la DB : produire
une version MUTÉE ou CROISÉE reste une opération sur des DONNÉES ; la
faire ENTRER dans l'historique versionné reste
`GenomeStoreService.propose(origin=MUTATION/CROSSOVER, parent_version_id=...)`,
jamais automatique — aucune promotion, aucun raccordement à `AgentRuntime`.

Déterministe pour une graine donnée — reproductible, jamais un résultat qui
varie d'une exécution à l'autre pour les mêmes données."""

from __future__ import annotations

import random
from typing import Any

JsonLeaf = bool | int | float | str | None


def mutate(genome: dict[str, Any], *, rate: float, seed: int, magnitude: float = 0.1) -> dict[str, Any]:
    """Mutation ponctuelle : chaque feuille numérique (int/float, jamais un
    bool traité comme un entier) a une probabilité `rate` d'être perturbée
    de `± magnitude` (relatif) ; chaque feuille booléenne a la même
    probabilité d'être inversée — « mutations structurales appropriées au
    type », jamais un traitement uniforme aveugle au type de la donnée.
    Chaînes/`None`/structure du dict : jamais mutés (pas de contenu à
    inventer). Retourne un NOUVEAU dict — `genome` n'est jamais modifié en
    place."""
    if not (0.0 <= rate <= 1.0):
        raise ValueError("rate doit être dans [0.0, 1.0]")
    if magnitude < 0.0:
        raise ValueError("magnitude doit être >= 0.0")
    rng = random.Random(seed)
    return _mutate_value(genome, rate=rate, magnitude=magnitude, rng=rng)


def _mutate_value(value: Any, *, rate: float, magnitude: float, rng: random.Random) -> Any:
    if isinstance(value, dict):
        return {k: _mutate_value(v, rate=rate, magnitude=magnitude, rng=rng) for k, v in value.items()}
    if isinstance(value, list):
        return [_mutate_value(v, rate=rate, magnitude=magnitude, rng=rng) for v in value]
    if isinstance(value, bool):
        return (not value) if rng.random() < rate else value
    if isinstance(value, int):
        if rng.random() >= rate:
            return value
        delta = value * rng.uniform(-magnitude, magnitude)
        if delta == 0:
            delta = rng.choice((-1, 1))
        return int(round(value + delta))
    if isinstance(value, float):
        if rng.random() >= rate:
            return value
        return value * (1.0 + rng.uniform(-magnitude, magnitude))
    return value


def crossover(parent_a: dict[str, Any], parent_b: dict[str, Any], *, seed: int) -> dict[str, Any]:
    """Croisement uniforme : pour chaque clé présente dans les DEUX parents,
    hérite de A ou B avec 50/50 (récursivement si les deux valeurs sont des
    dict — mixage structurel à toute profondeur) ; pour une clé absente
    d'un seul parent, hérite directement de celui qui la porte — jamais une
    valeur inventée. Retourne un NOUVEAU dict, aucun parent n'est modifié."""
    rng = random.Random(seed)
    return _crossover_value(parent_a, parent_b, rng=rng)


def _crossover_value(a: Any, b: Any, *, rng: random.Random) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        child: dict[str, Any] = {}
        for key in sorted(set(a) | set(b)):
            if key in a and key in b:
                child[key] = _crossover_value(a[key], b[key], rng=rng)
            elif key in a:
                child[key] = a[key]
            else:
                child[key] = b[key]
        return child
    return a if rng.random() < 0.5 else b
