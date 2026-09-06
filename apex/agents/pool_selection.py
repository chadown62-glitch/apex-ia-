"""Sélection déterministe d'un `ComputePool` capable de sandbox — factorisé
hors d'`apex.metalearner.autonomy` (passe « Autonomous Learning Trigger »,
seul consommateur jusqu'ici) car `apex.metalearner.evolution.
PopulationEvolutionService` (passe « Real Clone Population + Evolution
Loop ») a exactement le même besoin : provisionner un clone jetable sans
jamais fabriquer un `pool_id`. Deux consommateurs réels indépendants — le
factoriser évite le doublon qu'ADR-0007 interdit, jamais une abstraction
spéculative."""

from __future__ import annotations

from apex.config.schema import ApexConfig, Capability, ComputePool, PoolStatus


def pick_sandbox_capable_pool(config: ApexConfig) -> ComputePool:
    """Premier `ComputePool` ACTIVE déclarant `Capability.SANDBOX`, trié par
    `pool_id` (déterministe) — lève `ValueError` si aucun n'existe, jamais
    une valeur fabriquée."""
    candidates = sorted(
        (p for p in config.pools.values() if p.status is PoolStatus.ACTIVE and Capability.SANDBOX in p.capabilities),
        key=lambda p: p.pool_id,
    )
    if not candidates:
        raise ValueError("aucun ComputePool ACTIVE ne déclare la capability « sandbox »")
    return candidates[0]
