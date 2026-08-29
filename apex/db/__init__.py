"""Persistance APEX — phase 2.

Périmètre exact du brief (`docs/VISION_INTEGRATION.md` §5) : `EventSink`
(stub -> implémenté), `ComputePool` (table + `trust_level`), `audit_log`
en écriture seule. `PolicyEngine` et `SecretBroker` restent à l'état de
schéma — aucune logique n'est ajoutée ici pour eux.

Ce module ne contient aucune logique de Genome (ADR-0001) : c'est du Core,
modifiable uniquement par un humain.
"""

from __future__ import annotations
