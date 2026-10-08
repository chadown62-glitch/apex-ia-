"""GET /status — état réel de chaque composant, pour le Panel `APEX.exe`.

Authentifiée comme toutes les routes (même jeton `.apex/api-token`). Rapporte les versions (APEX,
API, protocole Panel) pour que le Panel détecte une incompatibilité avant d'interpréter la réponse,
et l'état de chaque composant MESURÉ à l'instant (`apex.obs.component_checks`), plus l'interface web
servie par APEX (`apex.api.ui`). `overall` est LE verdict partagé par le Panel et l'interface web :
READY seulement si chaque composant rapporté est prêt, DEGRADED sinon (avec lesquels) — une API qui
répond ne peut jamais être « bloquée » : un démarrage refusé par le Startup Gate n'atteint jamais ce
point, c'est le Panel qui l'affiche. `gate` rappelle la décision du dernier rapport du Gate, avec la
phase 2 À PART (autoriser le démarrage n'autorise pas la phase 2). `/health` reste inchangé."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from apex.api import ui
from apex.api.schemas import DataEnvelope
from apex.obs import component_checks
from apex.version import API_VERSION, PANEL_PROTOCOL, __version__

router = APIRouter(prefix="/status", tags=["status"])

_STARTED_AT = datetime.now(UTC)


def overall(components: list[dict[str, Any]]) -> dict[str, Any]:
    not_ready = [c["component"] for c in components if c.get("state") != component_checks.READY
                 and c["component"] not in component_checks.informational()]
    return {"verdict": "degraded" if not_ready else "ready", "not_ready": not_ready}


def gate_summary(report_path: Path | None = None) -> dict[str, Any] | None:
    """Décision du dernier rapport du Gate (écrit par `apex serve` au démarrage) ; `None` s'il est
    illisible — jamais une décision supposée."""
    from apex.gate.report import DEFAULT_REPORT_PATH

    try:
        report = json.loads(Path(report_path or DEFAULT_REPORT_PATH).read_text(encoding="utf-8"))
        startup = report["startup"]
        return {
            "startup_authorized": bool(startup["authorized"]),
            "phase_2_authorized": bool(report["phase_2_authorized"]),
            "startup_unverified": [c["check_id"] for c in startup.get("unverified", [])],
            "generated_at": report.get("generated_at"),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


@router.get("", response_model=DataEnvelope[dict[str, Any]])
def get_status() -> DataEnvelope[dict[str, Any]]:
    ui_available, ui_detail = ui.ui_state()
    components = [
        {
            "component": "api",
            "state": component_checks.READY,
            "detail": "requête authentifiée servie",
            "latency_ms": None,
        },
        *(c.to_dict() for c in component_checks.collect()),
        {
            "component": "ui",
            "state": component_checks.READY if ui_available else component_checks.OFFLINE,
            "detail": ui_detail,
            "latency_ms": None,
        },
    ]
    data = {
        "service": "apex",
        "versions": {"apex": __version__, "api": API_VERSION, "panel_protocol": PANEL_PROTOCOL},
        "backend": {"pid": os.getpid(), "started_at": _STARTED_AT.isoformat()},
        # Profil d'exécution (« server » : WSL/Docker ; « standalone » : APEX.exe autonome) et, pour le
        # Panel, les composants hors verdict (informatifs) ou sans objet dans ce profil (non rapportés).
        "profile": component_checks.profile(),
        "informational": sorted(component_checks.informational()),
        "not_applicable": sorted(component_checks.not_applicable()),
        "components": components,
        "overall": overall(components),
        "gate": gate_summary(),
        "measured_at": datetime.now(UTC).isoformat(),
    }
    return DataEnvelope(status="real", data=data)


@router.get("/distribution", response_model=DataEnvelope[dict[str, Any]])
def get_distribution() -> DataEnvelope[dict[str, Any]]:
    """Ce que CETTE distribution contient réellement : l'extension Recherche (routes brain, learning,
    population, self-model, weakness…) est absente du Runtime packagé ; l'inférence native exige ses
    bibliothèques. L'interface s'y fie pour ne jamais présenter une capacité absente comme disponible."""
    from apex.models.native import _libraries
    from apex.util.extensions import load_research_extension

    research = load_research_extension() is not None
    missing = _libraries()
    return DataEnvelope(status="real", data={
        "edition": "dev" if research else "runtime",
        "research": research,
        "native_inference": missing is None,
        "native_inference_detail": missing,
    })
