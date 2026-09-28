"""Fabrique de l'application FastAPI — backend en lecture seule du futur
APEX Control Center (docs/COGNITIVE_ARCHITECTURE.md §12).

Périmètre : `/health`, `/gate`, `/registry`, `/events`, `/audit`, `/agents`,
`/missions`, `/scheduler`, `/conversations` (Chat réel, passe « Première
interface utilisable »), `/projects` (structure minimale), `/agent-templates`
(DÉFINITIONS, jamais un spawn réel), `/models`/`/providers` (Model/Provider
Catalog, `apex.models.catalog`), `/configured-agents`/`/agent-teams` (passe
« Agents V1 » — `apex.agent_registry`, une entité `Agent` réellement
exécutable via le Model Router, distincte d'`/agents`), `/tasks`,
`/eval-runs`, `/weakness/{specialty}`, `/self-model/{specialty}`,
`POST /experiment-cycles` (passe « API Évaluation/Apprentissage » —
`apex/api/routes/evals.py`, expose TaskBank/EvalRunner/WeaknessDetector/
SelfModel/`run_experiment_cycle`, déjà réels depuis les phases 5/12/18/22,
jamais réimplémentés ici), `/learning/cycles`, `/learning/status`,
`/learning/history` (passe « Autonomous Learning Trigger » —
`apex/api/routes/learning.py`, expose `apex.metalearner.autonomy.
AutonomousLearningService`/`apex.metalearner.autonomous_scheduler.
AutonomousLearningScheduler`, orchestration seule au-dessus de
`run_experiment_cycle`, toujours `Subject.HUMAN`, jamais un champ
d'identité fourni par l'appelant HTTP) — les seules données/actions
réellement disponibles aujourd'hui.
Code/Design/Skills/Genome/Benchmarks/RAG/ToolRegistry N'ONT PAS de route
ici : leurs abstractions propriétaires restent `PLANNED` ou hors périmètre
de cette passe, et exposer une route qui ne peut renvoyer que des données
inventées serait exactement l'erreur que ce projet entier s'interdit.

Authentification (passe « API Authentication Hardening ») : `apex.api.auth.
require_api_token` est appliquée comme dépendance UNIFORME sur chaque routeur
inclus ci-dessous, sans exception (y compris `/health` — aucune route jugée
« assez anodine » pour être exemptée au cas par cas ; aucun healthcheck
d'infrastructure de ce dépôt n'appelle `/health` de ce processus sans jeton,
voir `docker-compose.yml`, qui ne sonde que postgres/redis/minio). Le jeton
prouve seulement « ce processus connaît le secret local » — il ne mappe
toujours pas l'appelant à un `Subject` de PolicyEngine (chaque route continue
d'agir comme `Subject.HUMAN`/`Subject.CORE` implicite, voir les docstrings de
`apex/api/routes/{agents,missions,conversations,audit}.py`, toujours exactes
sur ce point) : l'autorisation fine par identité reste un chantier séparé,
hors périmètre de cette étape — voir `apex/api/auth.py`.

CORS : le frontend (`npm run dev`, Vite) tourne sur une origine distincte du
backend (ports différents) — sans en-têtes CORS, AUCUNE requête du
navigateur n'atteint l'API, quelle que soit la route (constaté lors de la
validation « Première interface utilisable », un `curl` direct ne révèle
jamais cet écart : la politique CORS n'existe que côté navigateur). Origines
autorisées lues depuis `APEX_CORS_ALLOW_ORIGINS` (liste séparée par des
virgules) ; à défaut, seules les origines de développement local usuelles
(`5173`/`4173`, Vite dev/preview, sur `localhost` et `127.0.0.1`) sont
permises — jamais un `allow_origins=["*"]` qui masquerait un déploiement
distant mal configuré. `Authorization` est désormais listé dans
`allow_headers` : sans cela, un navigateur bloquerait lui-même l'en-tête
côté préflight avant même d'atteindre `require_api_token` — un frontend
qui n'attache pas encore ce jeton reste un chantier séparé (voir rapport de
cette étape), mais ce n'est jamais la politique CORS qui doit l'en empêcher.

Documentation interactive (`/docs`, `/redoc`, `/openapi.json`) : DÉSACTIVÉE
par défaut — ce sont des routes FastAPI internes, ajoutées en dehors de
`include_router`, qui ne portent JAMAIS les dépendances passées à
`include_router` (limitation documentée de FastAPI/Starlette) ; les laisser
actives par défaut exposerait toute la surface de l'API (chemins, schémas)
sans jeton. Réactivables explicitement en développement local via
`APEX_API_ENABLE_DOCS=1` — jamais par défaut.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apex.api.auth import require_api_token

_DEFAULT_DEV_ORIGINS = (
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
)

from apex.api.routes import (
    agent_registry,
    agent_templates,
    agents,
    audit,
    brain,
    capability,
    conversations,
    evals,
    events,
    gate,
    gauntlets,
    health,
    learning,
    learning_feedback,
    learning_sessions,
    missions,
    models,
    operator,
    population,
    projects,
    registry,
    scheduler,
)
from apex.version import __version__

API_PREFIX = "/api/v1"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Démarre/arrête `apex.metalearner.autonomous_scheduler.
    AutonomousLearningScheduler` — passe « Autonomous Learning Trigger ».
    `start()` est un no-op tant que `AutonomousLearningPolicy.enabled` reste
    `False` (défaut) : ce lifespan n'active JAMAIS l'apprentissage autonome
    par lui-même, il ne fait qu'appliquer la configuration existante. Note :
    aucun code de ce dépôt ne lance aujourd'hui réellement ce processus via
    un serveur ASGI (voir `apex/api/auth.py`) — ce lifespan ne s'exécute
    donc concrètement que sous un futur déploiement réel, ou sous un test
    qui utilise `TestClient` comme gestionnaire de contexte (`with
    TestClient(app) as client:`).

    `stop()` est bornée (`timeout_s` par défaut de `AutonomousLearningScheduler.
    stop`) — jamais un arrêt qui bloque indéfiniment ; un cycle déjà
    `RUNNING` continue jusqu'à sa fin naturelle, indépendant de ce thread
    (voir la docstring de `AutonomousLearningScheduler.stop`).

    Ne lève JAMAIS : une DB/config indisponible au démarrage du processus
    ne doit pas empêcher l'API de démarrer (même discipline que chaque
    route existante, qui dégrade en `status="unknown"` plutôt que de
    planter) — construire `get_learning_scheduler()` touche `load_config()`
    et `get_session_factory()`, qui peuvent légitimement échouer avant
    qu'aucune configuration DB ne soit fournie.

    Initialise aussi le jeton d'authentification (`get_or_create_api_token`)
    AVANT que le serveur ASGI ne commence à accepter des connexions — le
    protocole ASGI garantit que ce lifespan `startup` se termine avant que
    l'hôte (Uvicorn) n'ouvre le socket d'écoute. Sans cela, `.apex/api-token`
    n'était créé qu'à la toute première requête HTTP reçue (dépendance
    FastAPI `require_api_token`, exécutée par requête) — un contrôleur
    externe qui doit lire ce fichier AVANT de pouvoir faire sa toute première
    requête authentifiée (ex. le health check du Dashboard Windows,
    `ApexSetup.Dashboard.Services.ApiHealthChecker`, qui lit le jeton via
    `wsl.exe -- cat .apex/api-token` avant toute requête HTTP, précisément
    pour ne jamais sonder sans authentification) n'avait alors aucun moyen
    de déclencher cette création — un vrai blocage au tout premier
    démarrage, jamais rencontré jusqu'ici car, comme la note ci-dessus le
    documente, aucun code de ce dépôt ne lançait réellement le serveur ASGI
    avant `apex serve` (N+15). Comportement d'authentification lui-même
    STRICTEMENT inchangé (même fonction, mêmes garanties, voir
    `apex.api.auth`) — seul le MOMENT de l'appel change. `OSError` avalée
    ici exactement comme pour le scheduler ci-dessous : un répertoire
    `.apex/` non inscriptible ne doit jamais empêcher l'API de démarrer ;
    `require_api_token` retentera cette même création à la première requête
    et échouera alors bruyamment (500, jamais un contournement silencieux —
    voir `TestConfigurationFailure` dans `tests/test_api_auth.py`, inchangé
    par cet ajout)."""
    from apex.api.auth import get_or_create_api_token
    from apex.api.dependencies import get_learning_scheduler

    try:
        get_or_create_api_token()
    except OSError:
        pass

    scheduler_service = None
    try:
        scheduler_service = get_learning_scheduler()
        scheduler_service.start()
    except Exception:
        pass
    try:
        yield
    finally:
        if scheduler_service is not None:
            scheduler_service.stop()


def create_app() -> FastAPI:
    enable_docs = os.environ.get("APEX_API_ENABLE_DOCS") == "1"
    app = FastAPI(
        title="APEX Control Center API",
        version=__version__,
        description=(
            "Backend en lecture seule — chaque endpoint conserve son statut "
            "REAL/SIMULATED/UNKNOWN. Voir docs/COGNITIVE_ARCHITECTURE.md."
        ),
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
        lifespan=_lifespan,
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
        allow_headers=["Content-Type", "Authorization"],
    )

    # Dépendance UNIFORME (§ authentification, voir docstring du module) :
    # un seul `Depends(...)` posé au niveau de chaque inclusion de routeur,
    # jamais au cas par cas route par route — même discipline explicite que
    # la boucle elle-même (aucun routeur ajouté ici ne peut oublier la
    # dépendance sans le faire visiblement, à cet unique endroit).
    auth_dependency = Depends(require_api_token)
    for router in (
        health.router, gate.router, registry.router, events.router, audit.router,
        agents.router, missions.router, scheduler.router, operator.router,
        conversations.router, projects.router, agent_templates.router, models.router,
        agent_registry.router, evals.router, learning.router, population.router,
        learning_sessions.router, gauntlets.router, learning_feedback.router, capability.router,
        brain.router,
    ):
        app.include_router(router, prefix=API_PREFIX, dependencies=[auth_dependency])

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
