"""Fabrique de l'application FastAPI — backend en lecture seule du futur
APEX Control Center (docs/COGNITIVE_ARCHITECTURE.md §12).

Périmètre : `/health`, `/gate`, `/registry`, `/events`, `/audit`, `/agents`,
`/missions`, `/scheduler` — les seules données/actions réellement disponibles
aujourd'hui.
Learning/Code/Design/Projects/Research/Experiments/Benchmarks/Memory/Genome
N'ONT PAS de route ici : leurs abstractions propriétaires restent `PLANNED`,
et exposer une route qui ne peut renvoyer que des données inventées serait
exactement l'erreur que ce projet entier s'interdit.

Aucune authentification de l'appelant HTTP. C'est une limite réelle, pas un
oubli : aucun système ne mappe encore une requête à un `Subject` de
PolicyEngine — à traiter avant tout déploiement, hors périmètre de cette
étape (voir apex/api/routes/agents.py).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apex.api.routes import agents, audit, events, gate, health, missions, registry, scheduler
from apex.version import __version__

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="APEX Control Center API",
        version=__version__,
        description=(
            "Backend en lecture seule — chaque endpoint conserve son statut "
            "REAL/SIMULATED/UNKNOWN. Voir docs/COGNITIVE_ARCHITECTURE.md."
        ),
    )

    for router in (
        health.router, gate.router, registry.router, events.router, audit.router,
        agents.router, missions.router, scheduler.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Filet de sécurité : une exception qui atteint ici est un bug (les
        routes DB dégradent déjà elles-mêmes en `status: "unknown"`). Jamais
        de traceback brut renvoyé à l'appelant ; toujours la même enveloppe."""
        return JSONResponse(
            status_code=500,
            content={"status": "unknown", "data": None, "detail": f"{exc.__class__.__name__}: {exc}"},
        )

    return app


app = create_app()
