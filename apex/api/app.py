"""Fabrique de l'application FastAPI — backend en lecture seule du futur
APEX Control Center (docs/COGNITIVE_ARCHITECTURE.md §12).

Périmètre : `/health`, `/gate`, `/registry`, `/events`, `/audit`, `/agents`,
`/missions`, `/scheduler`, `/conversations` (Chat réel, passe « Première
interface utilisable »), `/projects` (structure minimale), `/agent-templates`
(DÉFINITIONS, jamais un spawn réel), `/models`/`/providers` (Model/Provider
Catalog, `apex.models.catalog`), `/configured-agents`/`/agent-teams` (passe
« Agents V1 » — `apex.agent_registry`, une entité `Agent` réellement
exécutable via le Model Router, distincte d'`/agents`) — les seules données/
actions réellement disponibles aujourd'hui.
Learning/Code/Design/Research/Experiments/Benchmarks/Memory/Genome N'ONT PAS
de route ici : leurs abstractions propriétaires restent `PLANNED`, et
exposer une route qui ne peut renvoyer que des données inventées serait
exactement l'erreur que ce projet entier s'interdit.

Aucune authentification de l'appelant HTTP. C'est une limite réelle, pas un
oubli : aucun système ne mappe encore une requête à un `Subject` de
PolicyEngine — à traiter avant tout déploiement, hors périmètre de cette
étape (voir apex/api/routes/agents.py).

CORS : le frontend (`npm run dev`, Vite) tourne sur une origine distincte du
backend (ports différents) — sans en-têtes CORS, AUCUNE requête du
navigateur n'atteint l'API, quelle que soit la route (constaté lors de la
validation « Première interface utilisable », un `curl` direct ne révèle
jamais cet écart : la politique CORS n'existe que côté navigateur). Origines
autorisées lues depuis `APEX_CORS_ALLOW_ORIGINS` (liste séparée par des
virgules) ; à défaut, seules les origines de développement local usuelles
(`5173`/`4173`, Vite dev/preview, sur `localhost` et `127.0.0.1`) sont
permises — jamais un `allow_origins=["*"]` qui masquerait un déploiement
distant mal configuré.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_DEFAULT_DEV_ORIGINS = (
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
)

from apex.api.routes import (
    agent_registry,
    agent_templates,
    agents,
    audit,
    conversations,
    events,
    gate,
    health,
    missions,
    models,
    projects,
    registry,
    scheduler,
)
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

    configured = os.environ.get("APEX_CORS_ALLOW_ORIGINS")
    allow_origins = (
        [o.strip() for o in configured.split(",") if o.strip()]
        if configured
        else list(_DEFAULT_DEV_ORIGINS)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    for router in (
        health.router, gate.router, registry.router, events.router, audit.router,
        agents.router, missions.router, scheduler.router,
        conversations.router, projects.router, agent_templates.router, models.router,
        agent_registry.router,
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
