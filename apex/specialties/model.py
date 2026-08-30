"""Modèle d'une Specialty backend — registre, pas une implémentation.

`Specialty` != `Domain` : une Specialty est un module fonctionnel d'APEX
(Code Intelligence, Research, Learning Center...), un regroupement de
capacités/outils/types de clones compatibles autour d'un même objet
d'interface. `Domain` (Maths, Physique, Cybersécurité — voir
`docs/COGNITIVE_ARCHITECTURE.md` §3) est un domaine d'apprentissage/
connaissance, un concept distinct, pas encore implémenté. Une Specialty peut
un jour couvrir plusieurs Domain ; l'inverse n'a pas de sens. Ne jamais
fusionner les deux modules ni les deux vocabulaires.

`dev_stage` réutilise `apex.interfaces.registry.ImplStatus` plutôt que
d'inventer un troisième vocabulaire d'état : la question posée
("cette Specialty a-t-elle une implémentation réelle utilisable ?") est
exactement celle qu'`ImplStatus` répond déjà pour toute abstraction APEX
(ADR-0007). Un vocabulaire parallèle aurait dupliqué ce qu'ADR-0007 interdit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apex.interfaces.registry import ImplStatus
from apex.policy.model import Subject


@dataclass(frozen=True)
class Specialty:
    id: str
    name: str
    description: str
    dev_stage: ImplStatus
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    tools: tuple[str, ...] = field(default_factory=tuple)
    required_permissions: tuple[str, ...] = field(default_factory=tuple)
    compatible_agent_types: tuple[Subject, ...] = field(default_factory=tuple)

    @property
    def is_supported(self) -> bool:
        """Une mission peut réellement lui être assignée — IMPLEMENTED ou STUB,
        jamais DEFINED/PLANNED (même seuil que `Abstraction.is_available`,
        apex.interfaces.registry)."""
        return self.dev_stage in (ImplStatus.IMPLEMENTED, ImplStatus.STUB)
