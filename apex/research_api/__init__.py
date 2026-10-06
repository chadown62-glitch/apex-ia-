"""Extension de RECHERCHE d'APEX (06/10/2026) — absente du Runtime livré.

Regroupe ce qui expose les paquets de recherche (`metalearner`, `neural`, `weakness`, `selfmodel`, ...) :
routes HTTP (apprentissage autonome, sessions, populations, gauntlets, capacités, cerveau, faiblesses,
auto-modèle, cycles d'expérience), commandes CLI (`learn`, `learning`, `solve`), planificateur d'apprentissage
autonome et sonde du cerveau. Chargée par `apex.util.extensions.load_research_extension` ; le Runtime fonctionne
sans elle (boucle de tâche, Chat, missions, modèles, sandbox, Judge, Experience).
"""

from __future__ import annotations

from typing import Any


def routers() -> list[Any]:
    from apex.research_api.routes import (
        brain,
        capability,
        evals_research,
        gauntlets,
        learning,
        learning_feedback,
        learning_sessions,
        population,
    )

    return [
        evals_research.router, learning.router, population.router, learning_sessions.router,
        gauntlets.router, learning_feedback.router, capability.router, brain.router,
    ]


def register_cli(sub: Any) -> None:
    from apex.research_api.cli import register

    register(sub)


def start_background() -> Any:
    """Démarre le planificateur d'apprentissage autonome (no-op tant que la politique le désactive) ; retourne
    un objet à arrêter (`stop()`), ou lève — l'appelant décide (le lifespan de l'API n'empêche jamais le
    démarrage, voir `apex.api.app._lifespan`)."""
    from apex.research_api.dependencies import get_learning_scheduler

    scheduler = get_learning_scheduler()
    scheduler.start()
    return scheduler


def check_brain(path: Any) -> dict[str, Any]:
    """Champion du cerveau neuronal dans `path` (sonde de statut) — voir `apex.obs.component_checks`."""
    from apex.neural.live import BrainWorkdir

    brain = BrainWorkdir(path)
    return brain.champion(brain.registry_entries(), brain.journal())
