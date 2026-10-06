"""Fournisseurs des services de RECHERCHE (apprentissage autonome, sessions, populations) — retirés de
`apex.api.dependencies` (runtime) le 06/10/2026. Corps inchangés ; `get_session_factory` est relu à CHAQUE appel
dans `apex.api.dependencies` (les tests et le runtime le remplacent à cet endroit unique)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from apex.api import dependencies as _core

if TYPE_CHECKING:
    from apex.metalearner.autonomous_scheduler import AutonomousLearningScheduler
    from apex.metalearner.autonomy import AutonomousLearningService
    from apex.metalearner.evolution import PopulationEvolutionService
    from apex.metalearner.learning_orchestrator import LearningOrchestrator


def get_session_factory():  # noqa: ANN201 — délègue au fournisseur runtime, jamais une copie
    return _core.get_session_factory()


def get_evolution_service() -> "PopulationEvolutionService":
    """PAS de singleton mis en cache — même choix qu'`get_session_factory` :
    `PopulationEvolutionService` (passe « Real Clone Population + Evolution
    Loop ») est un service SANS état en mémoire entre deux appels (contraste
    volontaire avec `AutonomousLearningService`/`SchedulerService`, qui
    suivent un cycle/`ExecutionRegistry` EN MÉMOIRE devant survivre entre
    requêtes) — chaque `evolve_generation()` lit/écrit uniquement via la DB
    (`population_members`, `genome_versions`, `agents`), donc reconstruire
    l'objet à chaque requête est correct et bon marché."""
    from apex.agents.runtime import AgentRuntime
    from apex.config.loader import load_config
    from apex.metalearner.evolution import PopulationEvolutionService

    session_factory = get_session_factory()
    config = load_config()
    return PopulationEvolutionService(session_factory, agent_runtime=AgentRuntime(session_factory), config=config)


def get_learning_orchestrator() -> "LearningOrchestrator":
    """PAS de singleton mis en cache — même raisonnement qu'`get_evolution_
    service` : `LearningOrchestrator` ne porte AUCUN état mémoire entre deux
    appels (contrairement à `AutonomousLearningService`/`SchedulerService`)
    — toute session vit dans `learning_sessions` (DB), y compris
    l'exclusion mutuelle inter-processus (index UNIQUE partiel, jamais un
    verrou en mémoire). Chaque `start_session()`/`resume_session()` lance
    son propre thread démon borné — jamais un scheduler, jamais un état
    partagé entre requêtes qui exigerait un singleton."""
    from apex.agents.runtime import AgentRuntime
    from apex.config.loader import load_config
    from apex.metalearner.learning_orchestrator import LearningOrchestrator

    session_factory = get_session_factory()
    config = load_config()
    return LearningOrchestrator(session_factory, agent_runtime=AgentRuntime(session_factory), config=config)


#: Même discipline « singleton paresseux » que `get_scheduler_service` —
#: `AutonomousLearningService` suit un cycle EN MÉMOIRE (`threading.Lock` +
#: `CycleStatus`) qui doit rester LE MÊME objet entre deux requêtes HTTP pour
#: que la protection anti-doublon tienne. Une reconstruction par requête
#: perdrait le suivi du cycle `RUNNING` à chaque appel.
_learning_service: "AutonomousLearningService | None" = None
_learning_scheduler: "AutonomousLearningScheduler | None" = None
_learning_lock = threading.Lock()


def get_learning_service() -> "AutonomousLearningService":
    global _learning_service
    if _learning_service is not None:
        return _learning_service
    with _learning_lock:
        if _learning_service is not None:
            return _learning_service
        from apex.agents.runtime import AgentRuntime
        from apex.config.loader import load_config
        from apex.metalearner.autonomy import AutonomousLearningService

        session_factory = get_session_factory()
        config = load_config()
        _learning_service = AutonomousLearningService(
            session_factory, agent_runtime=AgentRuntime(session_factory), config=config,
        )
        return _learning_service


def get_learning_scheduler() -> "AutonomousLearningScheduler":
    """Construit — sans le démarrer — le Scheduler d'apprentissage autonome.
    Démarrer réellement le thread (`AutonomousLearningScheduler.start()`,
    no-op si `enabled=False`) reste la responsabilité explicite du lifespan
    d'`apex.api.app`, jamais de cette fonction elle-même — une simple
    lecture de statut (`GET /learning/status`) ne doit jamais avoir pour
    effet de bord de démarrer un thread de fond."""
    global _learning_scheduler
    if _learning_scheduler is not None:
        return _learning_scheduler
    # `get_learning_service()` gère déjà sa propre construction verrouillée
    # (memoization interne) — appelée HORS de `_learning_lock` ci-dessous
    # pour ne jamais tenter de ré-acquérir le même verrou non réentrant
    # (`threading.Lock`) depuis le même thread, ce qui bloquerait
    # indéfiniment (auto-interblocage).
    service = get_learning_service()
    with _learning_lock:
        if _learning_scheduler is not None:
            return _learning_scheduler
        from apex.config.loader import load_config
        from apex.metalearner.autonomous_scheduler import AutonomousLearningScheduler

        _learning_scheduler = AutonomousLearningScheduler(service, config=load_config())
        return _learning_scheduler


def reset_learning_service_for_tests() -> None:
    """Réservé aux tests — même raison que `reset_scheduler_service_for_tests`
    (base PostgreSQL recréée sous le même nom entre deux tests)."""
    global _learning_service, _learning_scheduler
    with _learning_lock:
        if _learning_scheduler is not None:
            _learning_scheduler.stop()
        _learning_scheduler = None
        _learning_service = None
