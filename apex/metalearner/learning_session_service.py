"""`LearningSessionService` — persistance et bookkeeping d'une session
d'apprentissage multi-génération (passe « Autonomous Multi-Generation
Learning Orchestrator »).

Ce module NE lance AUCUNE génération, NE spawn AUCUN clone — il ne fait
qu'enregistrer/dérouler le cycle de vie d'une session déjà pilotée par
`apex.metalearner.learning_orchestrator.LearningOrchestrator`, exactement
le même rôle de bookkeeping pur que `apex.metalearner.population_service.
PopulationService` vis-à-vis d'`apex.metalearner.evolution.
PopulationEvolutionService`."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.metalearner.learning_session_model import LearningSession, LearningSessionStatus
from apex.metalearner.learning_session_repository import LearningSessionRepository

#: Générations minimum/maximum absolues acceptées par CE service, quel que
#: soit le plafond administrateur (`PopulationPolicy.max_session_generations`)
#: — une seconde ligne de défense, jamais la seule (§8 de la mission :
#: « jamais une boucle autonome non bornée »).
_MIN_GENERATIONS = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningSessionService:
    def __init__(self, session_factory: Any) -> None:
        self._repo = LearningSessionRepository(session_factory)

    # -- démarrage / reprise ---------------------------------------------------

    def start(
        self, *, specialty: str, max_generations: int, created_by: str, convergence_patience: int = 0,
        max_generations_ceiling: int,
    ) -> LearningSession:
        """Lève `ValueError` si `max_generations` est hors bornes (`1` à
        `max_generations_ceiling`, le plafond ADMINISTRATEUR — voir
        `PopulationPolicy.max_session_generations`) — jamais une valeur
        silencieusement plafonnée. Lève `LearningSessionAlreadyRunning`
        (traduit par le repository depuis l'index UNIQUE partiel réel en
        base) si une session est déjà `running` pour cette spécialité —
        garantie qui tient même avec plusieurs processus/workers API."""
        if not (_MIN_GENERATIONS <= max_generations <= max_generations_ceiling):
            raise ValueError(
                f"max_generations doit être compris entre {_MIN_GENERATIONS} et "
                f"{max_generations_ceiling} (plafond administrateur, "
                f"voir population.max_session_generations) — reçu {max_generations}"
            )
        if convergence_patience < 0:
            raise ValueError("convergence_patience doit être >= 0 (0 = convergence désactivée)")
        session = LearningSession.new(
            specialty=specialty, max_generations=max_generations, created_by=created_by,
            convergence_patience=convergence_patience,
        )
        return self._repo.create(session)

    def resume(self, session_id: str) -> LearningSession:
        """Lève `LookupError` si la session n'existe pas, `ValueError` si
        elle n'est pas dans un état reprenable (`failed`/`cancelled` — une
        session déjà `running`/`completed` ne se reprend jamais).
        `LearningSessionAlreadyRunning` si une AUTRE session de la même
        spécialité est déjà `running` (même garantie SGBD que `start`)."""
        session = self._repo.get(session_id)
        if session is None:
            raise LookupError(f"session d'apprentissage « {session_id} » introuvable")
        resumable = (LearningSessionStatus.FAILED, LearningSessionStatus.CANCELLED)
        if session.status not in resumable:
            raise ValueError(
                f"session « {session_id} » dans l'état « {session.status.value} », non reprenable "
                f"(seuls {[s.value for s in resumable]} le sont)"
            )
        updated = self._repo.transition_to_running(session_id, from_statuses=resumable, specialty=session.specialty)
        if updated is None:
            raise ValueError(
                f"session « {session_id} » déjà reprise/modifiée entre-temps — course perdue"
            )
        return updated

    # -- progression -----------------------------------------------------------

    def record_generation(
        self,
        session_id: str,
        *,
        generations_completed: int,
        best_member_id: str | None,
        best_fitness: float | None,
        best_genome_version_id: str | None,
        improved: bool,
    ) -> LearningSession | None:
        """`None` si la session n'est plus `running` (annulation/panne
        concurrente détectée par un AUTRE appelant entre-temps) — jamais
        une exception, la boucle appelante (`LearningOrchestrator._run_loop`)
        relit simplement l'état réel au tour suivant."""
        current = self._repo.get(session_id)
        if current is None:
            return None
        generations_since_improvement = 0 if improved else current.generations_since_improvement + 1
        return self._repo.record_generation(
            session_id, generations_completed=generations_completed,
            generations_since_improvement=generations_since_improvement,
            best_member_id=best_member_id, best_fitness=best_fitness,
            best_genome_version_id=best_genome_version_id,
        )

    def finish(
        self, session_id: str, *, status: LearningSessionStatus, stop_reason: str, error: str | None = None,
    ) -> LearningSession | None:
        return self._repo.finish(session_id, status=status, stop_reason=stop_reason, error=error, completed_at=_utcnow())

    def request_cancel(self, session_id: str) -> LearningSession:
        """No-op silencieux si la session n'est pas `running` (déjà
        terminale, ou déjà `cancel_requested`) — jamais une erreur pour un
        appel redondant, même discipline qu'`AutonomousLearningService.
        cancel()`/`PopulationService.set_status` sur un membre `retired`."""
        updated = self._repo.request_cancel(session_id)
        if updated is not None:
            return updated
        current = self._repo.get(session_id)
        if current is None:
            raise LookupError(f"session d'apprentissage « {session_id} » introuvable")
        return current

    # -- lecture -----------------------------------------------------------------

    def get(self, session_id: str) -> LearningSession | None:
        return self._repo.get(session_id)

    def list_by_specialty(self, specialty: str, *, limit: int = 50) -> list[LearningSession]:
        return self._repo.list_by_specialty(specialty, limit=limit)

    def list_all(self, *, limit: int = 50) -> list[LearningSession]:
        return self._repo.list_all(limit=limit)
