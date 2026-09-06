"""Genome -> configuration de clone -> `AgentRuntime` — la frontière RÉELLE,
minimale, entre le contenu d'une `GenomeVersion` et un comportement observable
d'exécution (passe « Real Clone Population + Evolution Loop »).

Jusqu'à cette passe, `GenomeVersion.content` était une donnée totalement
inerte (voir la docstring d'`apex.genome.service` : « aucun raccordement à
`AgentRuntime`/`Clone.genome_ref` »). Ce module ne résout PAS tout le
problème (aucun paramètre de stratégie/modèle/skill n'est encore branché —
voir `docs/DEVELOPMENT_HANDOFF.md`), seulement le sous-ensemble qui a déjà un
point d'entrée réel et sûr à modifier : `apex.sandbox.model.ResourceLimits`,
dont `EvalRunnerService.run()` construit une instance PAR EXÉCUTION (jamais
partagée), et dont la docstring documente déjà explicitement l'intention :
« Un futur appelant [...] pourra les surcharger [...] ; ce n'est pas encore
le cas ». C'est ce futur appelant.

**Sécurité par construction, jamais par confiance** : le contenu d'un Genome
peut provenir d'une mutation automatique (`Subject.CORE`, jamais promue par
un humain avant application) — un genome ne peut donc JAMAIS élargir les
limites au-delà de ce que l'administrateur a déjà configuré
(`SandboxDefaults`/`config.yaml`). Chaque champ n'est appliqué que s'il est
un NOMBRE fini strictement positif et STRICTEMENT INFÉRIEUR OU ÉGAL à la
valeur de base — jamais une valeur inventée, jamais une élévation.
`network` n'est jamais surchargeable (seul un administrateur choisit
`"proxy"`, jamais un Genome évolué automatiquement).
"""

from __future__ import annotations

import math
from typing import Any

from apex.sandbox.model import ResourceLimits

#: Clé de premier niveau du contenu JSON d'un Genome portant les overrides
#: d'exécution — absente ou mal formée : silencieusement ignorée (le Genome
#: reste une donnée FACULTATIVE, jamais une exigence sur sa forme).
_EXECUTION_KEY = "execution"

#: `(clé genome, attribut ResourceLimits)` — seuls des NOMBRES, jamais
#: `network` (choix d'administrateur seul, voir docstring module) ni
#: `pids_limit`/`disk_mb` (aucun besoin observé justifiant leur exposition —
#: ADR-0007, ne pas exposer un paramètre sans consommateur réel).
_OVERRIDABLE_FIELDS: tuple[str, ...] = ("timeout_s", "memory_mb", "cpu_cores")


def resolve_resource_limits(genome_content: dict[str, Any] | None, base: ResourceLimits) -> ResourceLimits:
    """Applique les overrides d'exécution d'un Genome à `base`, bornés pour
    ne jamais dépasser la configuration administrateur. Retourne toujours un
    NOUVEAU `ResourceLimits` — `base` n'est jamais muté. Contenu absent/mal
    formé -> `base` inchangé, jamais une exception (un Genome invalide ne
    doit jamais empêcher une exécution, seulement ne pas l'influencer)."""
    if not isinstance(genome_content, dict):
        return base
    execution = genome_content.get(_EXECUTION_KEY)
    if not isinstance(execution, dict):
        return base

    overrides: dict[str, Any] = {}
    for field in _OVERRIDABLE_FIELDS:
        candidate = execution.get(field)
        resolved = _clamp(candidate, ceiling=getattr(base, field))
        if resolved is not None:
            overrides[field] = resolved
    if not overrides:
        return base

    return ResourceLimits(
        cpu_cores=overrides.get("cpu_cores", base.cpu_cores),
        memory_mb=int(overrides.get("memory_mb", base.memory_mb)),
        pids_limit=base.pids_limit,
        timeout_s=overrides.get("timeout_s", base.timeout_s),
        disk_mb=base.disk_mb,
        network=base.network,
    )


def _clamp(value: Any, *, ceiling: float) -> float | None:
    """`None` si `value` n'est pas un nombre fini strictement positif, ou
    dépasse `ceiling` — jamais une élévation silencieuse, jamais un clamp
    optimiste qui laisserait passer une valeur invalide plafonnée."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    if value > ceiling:
        return None
    return float(value)
