"""GET /brain — état RÉEL du cerveau natif (boucle d'expérimentation, Judge, champion).

Le répertoire de travail observé est fixé côté serveur par `APEX_BRAIN_WORKDIR` (jamais choisi
par le client). Import de `apex.neural` différé dans les routes : l'API reste importable sans
l'extra `neural`, la route dégrade alors en `status: "unknown"` avec la raison.
Une chaîne de hachage rompue (journal ou registre) n'est jamais masquée : `unknown` + la cause."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from apex.api.schemas import DataEnvelope

router = APIRouter(prefix="/brain", tags=["brain"])

ENV_WORKDIR = "APEX_BRAIN_WORKDIR"


def _observed() -> tuple[Any, str | None]:
    configured = os.environ.get(ENV_WORKDIR)
    if not configured:
        return None, f"{ENV_WORKDIR} non configuré : aucune boucle observée"
    try:
        from apex.neural.live import BrainWorkdir
    except ImportError as exc:
        return None, f"cerveau natif indisponible ({exc})"
    return BrainWorkdir(Path(configured)), None


def _integrity_errors() -> tuple[type[Exception], ...]:
    from apex.neural.loop.journal import JournalCorruptedError
    from apex.neural.registry import RegistryCorruptedError

    return (JournalCorruptedError, RegistryCorruptedError)


@router.get("", response_model=DataEnvelope[dict[str, Any]])
def brain_state() -> DataEnvelope[dict[str, Any]]:
    brain, problem = _observed()
    if brain is None:
        return DataEnvelope(status="unknown", data=None, detail=problem)
    try:
        state = brain.state()
    except _integrity_errors() as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"intégrité rompue : {exc}")
    if state.get("status") != "real":
        return DataEnvelope(status="unknown", data=None, detail=state.get("reason"))
    return DataEnvelope(status="real", data=state)


@router.get("/events", response_model=DataEnvelope[list[dict[str, Any]]])
def brain_events(
    after: int = Query(default=-1, ge=-1), limit: int = Query(default=200, ge=1, le=1000)
) -> DataEnvelope[list[dict[str, Any]]]:
    """Événements du journal de décision, dans l'ordre, après le numéro de séquence `after`."""
    brain, problem = _observed()
    if brain is None:
        return DataEnvelope(status="unknown", data=None, detail=problem)
    try:
        events = brain.events(after=after, limit=limit)
    except _integrity_errors() as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"intégrité rompue : {exc}")
    return DataEnvelope(status="real", data=events)
