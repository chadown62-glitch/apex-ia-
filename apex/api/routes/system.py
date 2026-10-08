"""POST /system/shutdown — arrêt propre d'APEX demandé par son Panel (authentifié par le jeton, comme toute
l'API ; voir `apex.api.lifecycle`). La réponse part AVANT l'arrêt effectif (le serveur termine les requêtes
en cours, dont celle-ci)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from apex.api import lifecycle
from apex.api.schemas import DataEnvelope

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/shutdown", status_code=202)
def shutdown() -> DataEnvelope[dict[str, Any]]:
    if not lifecycle.request_shutdown():
        raise HTTPException(status_code=409, detail="arrêt à distance indisponible pour ce serveur")
    return DataEnvelope(status="real", data={"shutdown": "requested"})
