"""`CapabilityStatus` — disponibilité RÉELLE d'une capacité, distincte de
tout autre vocabulaire fermé du dépôt (étape 10).

Répond à une question spécifique : « cette capacité peut-elle être utilisée,
et où ? » — jamais fusionné avec :
- `apex.obs.status.DataStatus` (REAL/SIMULATED/UNKNOWN) : une donnée
  AFFICHÉE est-elle réellement mesurée ?
- `apex.interfaces.registry.ImplStatus` (IMPLEMENTED/STUB/DEFINED/PLANNED) :
  le CODE existe-t-il ?
- `apex.memory.model.VerificationStatus` (VERIFIED/LIKELY/UNCERTAIN/
  DISPUTED/FALSE) : une AFFIRMATION est-elle vraie ?
- `apex.gate.model.GateStatus` : une mesure d'environnement est-elle
  conforme ?
- `apex.config.schema.PoolStatus` (ACTIVE/TARGET) : un `ComputePool`
  déclaré est-il une disponibilité réelle ou une cible future ?

Règle absolue (consigne étape 10, répétée dans chaque étape depuis
l'étape 7) : une capacité absente de la machine de développement actuelle
(DEV_MACHINE) est `UNAVAILABLE_ON_DEV`, **jamais** `UNSUPPORTED`.
`UNSUPPORTED` signifie une incompatibilité ARCHITECTURALE réellement
vérifiée — jamais une simple absence dans l'environnement de développement
temporaire. Docker absent ici ne rend pas APEX incapable de Docker ; un GPU
insuffisant ici ne borne pas définitivement APEX à ce GPU.
"""

from __future__ import annotations

from enum import Enum


class CapabilityStatus(str, Enum):
    #: Réellement disponible et vérifié, ICI, maintenant.
    AVAILABLE = "available"
    #: Absent de DEV_MACHINE (la machine de développement actuelle) —
    #: jamais une conclusion sur TARGET_MACHINE ni sur l'architecture.
    UNAVAILABLE_ON_DEV = "unavailable_on_dev"
    #: Prévu disponible sur TARGET_MACHINE (machine cible, pas encore
    #: auditée) — une déclaration, jamais une mesure sur cette machine.
    AVAILABLE_ON_TARGET = "available_on_target"
    #: Capacité nécessaire à une fonctionnalité donnée (matérielle ou
    #: logicielle), indépendamment de sa disponibilité actuelle.
    REQUIRED = "required"
    #: Prévue architecturalement, aucune implémentation n'existe encore.
    PLANNED = "planned"
    #: Incompatibilité ARCHITECTURALE réellement vérifiée — jamais utilisé
    #: pour une simple absence sur DEV_MACHINE (voir la docstring du module).
    UNSUPPORTED = "unsupported"
