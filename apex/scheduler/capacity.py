"""Vue de capacité du Scheduler.

Consigne étape 8 — distinction stricte, jamais mélangée :

1. Machine de développement actuelle / 2. machine cible APEX
   -> `apex.config.schema.PoolStatus` (ACTIVE / TARGET), déjà existant
   (correction architecturale précédente) — `active_pools`/`target_pools`
   ci-dessous ne font que le composer, jamais le redéfinir.
3. Capacités réellement observées sur la machine actuelle
   -> `apex.sandbox.detect.detect_capabilities()` (isolation, empirique,
   étape 7) — ce module ne mesure RIEN par lui-même, il ne reproduit pas
   cette sonde.
4. Capacités requises par une mission
   -> `apex.sandbox.model.ResourceLimits` (étape 7) — inchangé.
5. Capacités déclarées par les ComputePool
   -> `apex.config.schema.ComputePool` (`cpu_cores`, `ram_gb`), déjà
   existant — composé ici, jamais recalculé depuis le matériel courant.

`max_concurrent_missions` (le mécanisme de backpressure réellement
appliqué par `SchedulerService`) est un CHOIX DE DÉPLOIEMENT explicite
(`apex.config.schema.SchedulerPolicy`), jamais dérivé d'une lecture du
matériel de développement — même discipline que `HardwareThresholds`.
"""

from __future__ import annotations

from dataclasses import dataclass

from apex.config.schema import ApexConfig, ComputePool, PoolStatus


@dataclass(frozen=True)
class CapacityView:
    active_pools: tuple[ComputePool, ...]
    target_pools: tuple[ComputePool, ...]
    max_concurrent_missions: int
    running_missions: int

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent_missions - self.running_missions)


def build_capacity_view(config: ApexConfig, *, running_missions: int) -> CapacityView:
    pools = tuple(config.pools.values())
    return CapacityView(
        active_pools=tuple(p for p in pools if p.status is PoolStatus.ACTIVE),
        target_pools=tuple(p for p in pools if p.status is PoolStatus.TARGET),
        max_concurrent_missions=config.scheduler.max_concurrent_missions,
        running_missions=running_missions,
    )
