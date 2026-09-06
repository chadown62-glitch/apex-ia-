"""`LearningOrchestrator` — la première boucle MULTI-génération réelle
d'APEX (passe « Autonomous Multi-Generation Learning Orchestrator ») :

    specialty -> generation 0 -> fitness -> selection -> evolution
              -> generation 1 -> fitness -> selection -> evolution
              -> ...
              -> arrêt selon une politique explicite

Compose EXCLUSIVEMENT des primitives déjà réelles, n'en réimplémente
aucune — `apex.metalearner.evolution.PopulationEvolutionService.
evolve_generation` reste l'UNIQUE point d'exécution d'UNE génération
(sélection de parents, spawn de clone, mutation/croisement, expérience
réelle, fitness, sélection, Skill/Genome feedback) : ce module n'ajoute
QUE la politique de boucle (combien de générations, où s'arrêter,
comment reprendre), jamais une seconde implémentation de l'évolution
elle-même.

**Reprise sûre par construction** — `evolve_generation` dérive TOUJOURS
son numéro de génération de `population_members` déjà persisté
(`PopulationService.latest_generation`), jamais d'un paramètre fourni par
CE module : reprendre une session après une panne (génération 2 échouée,
0/1 déjà complètes) revient simplement à rappeler `evolve_generation` —
il reprend naturellement à la génération 2, sans jamais rejouer 0/1, sans
jamais dupliquer un membre/lignage/experiment_ref (l'idempotence de la
passe N+6, `PopulationService.add_member`, s'applique intégralement).

**Déclenchement HUMAIN, modèle N+4/N+6 inchangé** : `start_session` ne
prend AUCUN paramètre d'identité — `Subject.HUMAN` reste fourni en dur par
`evolve_generation` lui-même à chaque génération, jamais négociable depuis
l'extérieur.

**Annulation entre générations, jamais en cours de génération** (§7 de la
mission) : `_run_loop` relit l'état de la session (DB, jamais un drapeau
mémoire) AVANT de démarrer chaque nouvelle génération — une génération déjà
commencée va toujours à son terme (elle a sa propre valeur scientifique,
jamais interrompue à mi-chemin par un simple appel API).

**Exécution en tâche de fond, borné, jamais un démon** — un thread daemon
par session ACTIVE (même patron qu'`apex.metalearner.autonomy.
AutonomousLearningService`), jamais un scheduler/planificateur : ce module
ne démarre jamais lui-même une session, il ne fait qu'exécuter celle
explicitement demandée jusqu'à ce qu'elle s'arrête."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.evalrunner.model import EvalMethod
from apex.metalearner.evolution import PopulationEvolutionService
from apex.metalearner.learning_session_model import LearningSession, LearningSessionStatus
from apex.metalearner.learning_session_service import LearningSessionService

_EVENT_LOGGER = "apex.metalearner.learning_orchestrator"
#: Marge déterministe pour juger une amélioration « réelle » du meilleur
#: fitness — jamais un seuil ML arbitraire, seulement une tolérance
#: flottante minimale (comparer des `float` à l'égalité stricte serait
#: fragile). Non configurable : ce n'est pas une politique, seulement une
#: garde numérique.
_IMPROVEMENT_EPSILON = 1e-9


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningOrchestrator:
    def __init__(self, session_factory: Any, *, agent_runtime: AgentRuntime, config: Any = None) -> None:
        self._session_factory = session_factory
        self._config = config
        self._sessions = LearningSessionService(session_factory)
        self._evolution = PopulationEvolutionService(session_factory, agent_runtime=agent_runtime, config=config)

    # -- démarrage / reprise / annulation --------------------------------------

    def start_session(
        self,
        specialty: str,
        *,
        max_generations: int,
        convergence_patience: int = 0,
        current_level: int = 1,
        method: EvalMethod = EvalMethod.PRACTICE,
    ) -> LearningSession:
        """Lève `LearningSessionAlreadyRunning` si une session est déjà
        `running` pour cette spécialité (garanti par un index UNIQUE
        partiel en base, valide même avec plusieurs processus/workers API
        — voir `apex.db.models.LearningSessionRow`). Rend la main
        IMMÉDIATEMENT (`RUNNING`) ; la boucle s'exécute en tâche de fond."""
        from apex.policy.model import Subject

        ceiling = self._max_generations_ceiling()
        session = self._sessions.start(
            specialty=specialty, max_generations=max_generations, created_by=Subject.HUMAN.value,
            convergence_patience=convergence_patience, max_generations_ceiling=ceiling,
        )
        self._spawn_loop(session.id, current_level=current_level, method=method)
        return session

    def resume_session(
        self, session_id: str, *, current_level: int = 1, method: EvalMethod = EvalMethod.PRACTICE,
    ) -> LearningSession:
        """Lève `LookupError`/`ValueError`/`LearningSessionAlreadyRunning`
        — voir `LearningSessionService.resume`."""
        session = self._sessions.resume(session_id)
        self._spawn_loop(session.id, current_level=current_level, method=method)
        return session

    def cancel_session(self, session_id: str) -> LearningSession:
        """No-op silencieux si la session n'est pas `running` — jamais une
        erreur pour un appel redondant. La boucle en tâche de fond termine
        proprement la génération EN COURS avant de constater l'annulation
        (voir la docstring du module)."""
        return self._sessions.request_cancel(session_id)

    # -- lecture ----------------------------------------------------------------

    def get_session(self, session_id: str) -> LearningSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self, *, specialty: str | None = None, limit: int = 50) -> list[LearningSession]:
        if specialty is not None:
            return self._sessions.list_by_specialty(specialty, limit=limit)
        return self._sessions.list_all(limit=limit)

    # -- boucle interne -----------------------------------------------------------

    def _spawn_loop(self, session_id: str, *, current_level: int, method: EvalMethod) -> None:
        thread = threading.Thread(
            target=self._run_loop, args=(session_id,), kwargs={"current_level": current_level, "method": method},
            daemon=True, name=f"learning-session-{session_id[:8]}",
        )
        thread.start()

    def _run_loop(self, session_id: str, *, current_level: int, method: EvalMethod) -> None:
        while True:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.status is LearningSessionStatus.CANCEL_REQUESTED:
                self._sessions.finish(session_id, status=LearningSessionStatus.CANCELLED, stop_reason="cancelled")
                self._record_event("session.cancelled", session_id, session.specialty)
                return
            if session.status is not LearningSessionStatus.RUNNING:
                return  # déjà terminale (menée à bien par un autre chemin) — rien à faire.

            if session.generations_completed >= session.max_generations:
                self._sessions.finish(
                    session_id, status=LearningSessionStatus.COMPLETED, stop_reason="max_generations_reached",
                )
                self._record_event("session.completed", session_id, session.specialty)
                return

            try:
                result = self._evolution.evolve_generation(
                    session.specialty, current_level=current_level, method=method,
                )
            except Exception as exc:  # une génération qui lève est une panne de session, jamais silencieuse
                self._sessions.finish(
                    session_id, status=LearningSessionStatus.FAILED, stop_reason="generation_raised",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                self._record_event(
                    "session.failed", session_id, session.specialty,
                    {"reason": "generation_raised", "error": f"{exc.__class__.__name__}: {exc}"},
                )
                return

            if not result.members:
                if result.failures:
                    # Chaque membre tenté a réellement échoué (panne
                    # d'infrastructure — pas de Genome PROMOTED, pas de pool
                    # sandbox, ...) : une génération qui ne produit RIEN est
                    # une impasse, jamais un état silencieusement toléré.
                    self._sessions.finish(
                        session_id, status=LearningSessionStatus.FAILED,
                        stop_reason="generation_failed_all_members", error=result.failures[0].reason,
                    )
                    self._record_event(
                        "session.failed", session_id, session.specialty,
                        {"reason": "generation_failed_all_members", "error": result.failures[0].reason},
                    )
                else:
                    # Aucun échec — la population était déjà à son plafond
                    # (`PopulationCapExceeded` dès le premier membre) : un
                    # arrêt propre, pas une panne (§15 : le plafond est une
                    # protection, pas une erreur).
                    self._sessions.finish(
                        session_id, status=LearningSessionStatus.COMPLETED, stop_reason="population_at_capacity",
                    )
                    self._record_event("session.completed", session_id, session.specialty)
                return

            champion = result.champion
            best_fitness = session.best_fitness
            improved = champion is not None and (
                best_fitness is None or champion.fitness > best_fitness + _IMPROVEMENT_EPSILON
            )
            updated = self._sessions.record_generation(
                session_id, generations_completed=session.generations_completed + 1,
                best_member_id=champion.id if champion is not None else session.best_member_id,
                best_fitness=champion.fitness if champion is not None else session.best_fitness,
                best_genome_version_id=champion.genome_version_id if champion is not None else session.best_genome_version_id,
                improved=improved,
            )
            if updated is None:
                # `running`/`cancel_requested` couvrent déjà le cas
                # normal d'annulation (le compteur EST mis à jour même si
                # une annulation a été demandée pendant l'exécution — voir
                # `LearningSessionRepository.record_generation`) : `None`
                # ici signifie une transition CONCURRENTE plus rare encore
                # (panne détectée/session disparue ailleurs pendant cette
                # génération) — relit l'état réel au tour suivant plutôt
                # que de deviner, jamais une hypothèse fausse.
                continue

            self._record_event(
                "session.generation_completed", session_id, session.specialty,
                {
                    "generation": result.generation, "population_size": len(result.members),
                    "best_fitness": updated.best_fitness, "status": updated.status.value,
                },
            )

            if (
                updated.convergence_patience > 0
                and updated.generations_since_improvement >= updated.convergence_patience
            ):
                self._sessions.finish(session_id, status=LearningSessionStatus.COMPLETED, stop_reason="converged")
                self._record_event("session.completed", session_id, session.specialty, {"reason": "converged"})
                return
            # Sinon : boucle vers le tour suivant, où le statut à jour
            # (RUNNING ou CANCEL_REQUESTED) est relu en tête de boucle.

    # -- internes -----------------------------------------------------------

    def _max_generations_ceiling(self) -> int:
        from apex.config.loader import load_config

        config = self._config or load_config()
        return config.population.max_session_generations

    def _record_event(
        self, msg: str, session_id: str, specialty: str, extra: dict[str, Any] | None = None,
    ) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        context = {"session_id": session_id, "specialty": specialty, **(extra or {})}
        with session_scope(self._session_factory) as session:
            EventRepository(session).add(ts=_utcnow(), level="INFO", logger=_EVENT_LOGGER, msg=msg, context=context)
