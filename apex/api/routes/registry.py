"""GET /registry — état honnête des abstractions (apex.interfaces.registry).

Seule route dont l'enveloppe est TOUJOURS "real" : elle ne lit que la
mémoire du processus Python (`ABSTRACTIONS`), sans dépendance externe —
contrairement à `/events`, `/audit`, `/agents` (DB) ou `/gate` (fichier).
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import AbstractionOut, DataEnvelope, RegistryOut
from apex.interfaces.registry import ABSTRACTIONS, summary

router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("", response_model=DataEnvelope[RegistryOut])
def get_registry() -> DataEnvelope[RegistryOut]:
    items = [
        AbstractionOut(
            name=a.name,
            purpose=a.purpose,
            status=a.status.value,
            phase_defined=a.phase_defined,
            phase_implemented=a.phase_implemented,
            fallback=a.fallback,
            module=a.module,
            notes=a.notes,
        )
        for a in sorted(ABSTRACTIONS, key=lambda a: (a.phase_implemented, a.name))
    ]
    return DataEnvelope(status="real", data=RegistryOut(summary=summary(), abstractions=items))
