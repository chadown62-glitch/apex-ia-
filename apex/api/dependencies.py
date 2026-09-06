"""Accès DB paresseux — importé UNIQUEMENT par les routes qui en dépendent
(events, audit, agents, missions, scheduler, evals, learning), jamais par
apex.api.app ni par les autres routes.

Pas de mise en cache de la session factory : sa construction est bon marché
(SQLAlchemy ne se connecte pas avant le premier usage) et un cache figerait
la configuration/l'environnement lus au premier appel — un problème réel en
test, où l'environnement change entre deux cas.

`get_scheduler_service()` (étape 11) est volontairement DIFFÉRENT : c'est un
singleton applicatif RÉELLEMENT mis en cache, pour une raison distincte de la
session factory — `apex.scheduler.service.SchedulerService` est un service
à état (ThreadPoolExecutor borné + ExecutionRegistry en mémoire) qui doit
rester LE MÊME objet entre deux requêtes HTTP pour que ses garanties
(pas de double-soumission dans ce processus, capacité réellement bornée)
tiennent. Une reconstruction par requête recréerait un ThreadPoolExecutor à
chaque appel (fuite de threads) et viderait `ExecutionRegistry` à chaque
fois, cassant justement la propriété qu'il existe pour garantir. Cette
discipline « singleton paresseux » est exactement celle demandée par
l'arbitrage humain étape 11, point A : jamais recréé par requête, jamais
hébergé comme runtime par FastAPI (FastAPI ne fait que le CONSTRUIRE une
fois puis appeler ses méthodes — aucune boucle de fond).

Conséquence pour les tests : ce cache DOIT être vidé explicitement
(`reset_scheduler_service_for_tests()`) chaque fois qu'un test recrée la base
PostgreSQL sous le même nom (`tests/db_test_support.recreate_test_database`)
— un déploiement réel ne fait jamais ça pendant la vie d'un processus, donc
le runtime réel n'a jamais besoin de ce mécanisme ; seule la suite de tests
en a besoin, et l'utilise explicitement, jamais en silence.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from apex.errors import ApexError

if TYPE_CHECKING:
    from apex.metalearner.autonomous_scheduler import AutonomousLearningScheduler
    from apex.metalearner.autonomy import AutonomousLearningService
    from apex.metalearner.evolution import PopulationEvolutionService
    from apex.metalearner.learning_orchestrator import LearningOrchestrator
    from apex.scheduler.service import SchedulerService


class DatabaseUnavailable(Exception):
    """DB non configurée ou non joignable. Jamais renvoyée telle quelle à
    l'appelant HTTP : chaque route la traduit en `DataEnvelope(status="unknown")`."""


def get_session_factory() -> Any:
    from apex.config.loader import load_config
    from apex.db.engine import build_engine, build_session_factory, database_url_from_config

    try:
        config = load_config()
        url = database_url_from_config(config)
    except ApexError as exc:
        raise DatabaseUnavailable(str(exc)) from exc
    engine = build_engine(url)
    return build_session_factory(engine)


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


_scheduler_service: "SchedulerService | None" = None
_scheduler_lock = threading.Lock()


def get_scheduler_service() -> "SchedulerService":
    """Construit — UNE SEULE FOIS par processus — le Scheduler applicatif et
    le retourne. Double-checked locking : la construction (qui touche
    réellement la DB via `RecoveryService.reconcile_on_startup`, voir
    ci-dessous) n'a jamais lieu sous verrou hors du premier appel, mais deux
    threads ne peuvent jamais construire deux instances concurrentes.

    N'assigne le singleton global QU'APRÈS une construction intégralement
    réussie — si `RecoveryService.reconcile_on_startup()` échoue (DB
    injoignable), rien n'est mis en cache et l'appel SUIVANT retente
    proprement, plutôt que de figer un état construit-mais-jamais-réconcilié.
    """
    global _scheduler_service
    if _scheduler_service is not None:
        return _scheduler_service
    with _scheduler_lock:
        if _scheduler_service is not None:
            return _scheduler_service
        from apex.agents.runtime import AgentRuntime
        from apex.missions.service import MissionService
        from apex.scheduler.recovery import RecoveryService
        from apex.scheduler.service import SchedulerService

        session_factory = get_session_factory()
        mission_service = MissionService(session_factory)
        agent_runtime = AgentRuntime(session_factory)
        service = SchedulerService(
            session_factory, mission_service=mission_service, agent_runtime=agent_runtime,
        )
        # Réconciliation « au démarrage » (étape 8) : ici, PAS au sens du
        # processus FastAPI, mais au sens du Scheduler lui-même — la première
        # fois qu'il existe dans ce processus. Toute ligne RUNNING trouvée à
        # cet instant est orpheline par construction (voir apex.scheduler.
        # recovery), qu'un tick ait déjà eu lieu ou non — il n'y en a jamais
        # eu avant cette construction.
        RecoveryService(
            session_factory, mission_service=mission_service, agent_runtime=agent_runtime,
        ).reconcile_on_startup()
        _scheduler_service = service
        return _scheduler_service


def reset_scheduler_service_for_tests() -> None:
    """Réservé aux tests. Arrête proprement (executor + threads) le singleton
    courant s'il existe, puis vide le cache — le prochain appel à
    `get_scheduler_service()` en reconstruit un, contre la configuration/DB
    ACTUELLE. Jamais appelé par du code applicatif réel : voir la docstring
    du module pour pourquoi ce cache n'a normalement pas besoin d'être vidé
    pendant la vie d'un vrai processus."""
    global _scheduler_service
    with _scheduler_lock:
        if _scheduler_service is not None:
            _scheduler_service.shutdown(wait=True)
        _scheduler_service = None


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
