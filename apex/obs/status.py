"""REAL / SIMULATED / UNKNOWN — statut d'une donnée affichée, pas d'un composant.

Déplacé depuis `apex.agents.view` (où il était né) vers `apex.obs`, qui est
déjà l'emplacement des vocabulaires d'état transverses (`apex.obs.health.
Status`). Ce vocabulaire est celui déjà spécifié par
`docs/COGNITIVE_ARCHITECTURE.md` §12 pour l'interface — pas un troisième
vocabulaire inventé ici, sa première mise en code.

Trois vocabulaires coexistent délibérément, pour trois questions distinctes :
- `apex.gate.model.GateStatus` (`PASS`/`FAIL`/`DEGRADED`/`UNKNOWN`/`SIMULATED`) :
  la mesure d'environnement est-elle conforme ?
- `apex.obs.health.Status` (`OK`/`DEGRADED`/`FAILED`) : le composant
  fonctionne-t-il, là, maintenant ?
- `DataStatus` (ici) : la donnée qu'une interface affiche est-elle
  réellement mesurée, simulée, ou absente de toute mesure ?
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DataStatus(str, Enum):
    REAL = "real"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Measurement:
    """Une valeur, jamais affichée sans son statut. Une interface qui ignore
    `status` et affiche `value` seul reproduit exactement l'erreur que ce
    type existe pour empêcher."""

    value: Any
    status: DataStatus

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "status": self.status.value}

    @classmethod
    def real(cls, value: Any) -> Measurement:
        return cls(value, DataStatus.REAL)

    @classmethod
    def unknown(cls) -> Measurement:
        return cls(None, DataStatus.UNKNOWN)

    @classmethod
    def simulated(cls, value: Any) -> Measurement:
        return cls(value, DataStatus.SIMULATED)
