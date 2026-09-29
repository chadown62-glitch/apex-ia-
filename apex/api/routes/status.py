"""GET /status — état réel de chaque composant, pour le Panel `APEX.exe`.

Authentifiée comme toutes les routes (même jeton `.apex/api-token`). Rapporte les versions (APEX,
API, protocole Panel) pour que le Panel détecte une incompatibilité avant d'interpréter la réponse,
et l'état de chaque composant MESURÉ à l'instant (`apex.obs.component_checks`). `/health` reste
inchangé (liveness + base de données, utilisé par l'interface web)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from apex.api.schemas import DataEnvelope
from apex.obs import component_checks
from apex.version import API_VERSION, PANEL_PROTOCOL, __version__

router = APIRouter(prefix="/status", tags=["status"])

_STARTED_AT = datetime.now(UTC)


@router.get("", response_model=DataEnvelope[dict[str, Any]])
def get_status() -> DataEnvelope[dict[str, Any]]:
    components = [c.to_dict() for c in component_checks.collect()]
    data = {
        "service": "apex",
        "versions": {"apex": __version__, "api": API_VERSION, "panel_protocol": PANEL_PROTOCOL},
        "backend": {"pid": os.getpid(), "started_at": _STARTED_AT.isoformat()},
        "components": [
            {
                "component": "api",
                "state": component_checks.READY,
                "detail": "requête authentifiée servie",
                "latency_ms": None,
            },
            *components,
        ],
        "measured_at": datetime.now(UTC).isoformat(),
    }
    return DataEnvelope(status="real", data=data)
