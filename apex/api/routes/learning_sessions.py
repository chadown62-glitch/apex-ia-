"""POST /learning-sessions, GET /learning-sessions, GET /learning-sessions/
{id}, POST /learning-sessions/{id}/resume, POST /learning-sessions/{id}/
cancel — passe « Autonomous Multi-Generation Learning Orchestrator ».

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.learning_orchestrator.LearningOrchestrator` — même patron
que `apex.api.routes.population` vis-à-vis de `PopulationEvolutionService`.

**Sécurité — identique à `POST /population/{specialty}/evolve`** :
`POST /learning-sessions` ne prend AUCUN champ d'identité (`specialty`/
`max_generations`/`convergence_patience` seulement) — `LearningOrchestrator.
start_session` force `Subject.HUMAN` en dur en interne, jamais négociable
depuis l'extérieur. `resume`/`cancel` n'acceptent aucun corps de requête
non plus.

Distinction d'erreurs volontaire, même patron que `learning.py`/
`population.py` :
    - DB/infrastructure injoignable -> `DataEnvelope(status="unknown")`, 200.
    - entrée invalide (spécialité inconnue, `max_generations` hors bornes) ->
      422, vérifié explicitement AVANT le `except` générique.
    - une session est déjà `running` pour cette spécialité
      (`LearningSessionAlreadyRunning`, garanti par un index UNIQUE partiel
      RÉEL en base — voir `apex.db.models.LearningSessionRow`) -> 409.
    - session/état introuvable ou non repris-able (`LookupError`/
      `ValueError` levés par `LearningSessionService.resume`) -> 404/422.

Import différé d'`apex.metalearner` dans le corps des fonctions, jamais au
niveau module — même discipline qu'`apex.api.routes.learning`/`population`
(voir leur docstring : ce module ne fait pas partie de l'environnement/
artefact packagé, `tests/test_runtime_packaging.py`, hors périmètre de
cette passe).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import DataEnvelope, LearningSessionOut, LearningSessionStartIn

if TYPE_CHECKING:
    from apex.metalearner.learning_session_model import LearningSession

router = APIRouter(prefix="/learning-sessions", tags=["learning-sessions"])


def _to_session_out(session: "LearningSession") -> LearningSessionOut:
    return LearningSessionOut(
        id=session.id, specialty=session.specialty, status=session.status.value,
        max_generations=session.max_generations, generations_completed=session.generations_completed,
        convergence_patience=session.convergence_patience,
        generations_since_improvement=session.generations_since_improvement,
        best_member_id=session.best_member_id, best_fitness=session.best_fitness,
        best_genome_version_id=session.best_genome_version_id, stop_reason=session.stop_reason,
        error=session.error, created_by=session.created_by, created_at=session.created_at.isoformat(),
        completed_at=session.completed_at.isoformat() if session.completed_at else None,
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


@router.post("", response_model=DataEnvelope[LearningSessionOut])
def start_learning_session(payload: LearningSessionStartIn) -> DataEnvelope[LearningSessionOut]:
    """Rend la main IMMÉDIATEMENT (`RUNNING`) — la boucle multi-génération
    s'exécute en tâche de fond (voir la docstring de `LearningOrchestrator`
    pour la reprise sûre en cas de panne du processus)."""
    _validate_specialty(payload.specialty)
    from apex.errors import LearningSessionAlreadyRunning

    from apex.api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().start_session(
            payload.specialty, max_generations=payload.max_generations,
            convergence_patience=payload.convergence_patience,
        )
    except LearningSessionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_session_out(session))


@router.get("", response_model=DataEnvelope[list[LearningSessionOut]])
def list_learning_sessions(specialty: str | None = None, limit: int = 20) -> DataEnvelope[list[LearningSessionOut]]:
    from apex.api.dependencies import get_learning_orchestrator

    try:
        sessions = get_learning_orchestrator().list_sessions(specialty=specialty, limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_session_out(s) for s in sessions])


@router.get("/{session_id}", response_model=DataEnvelope[LearningSessionOut])
def get_learning_session(session_id: str) -> DataEnvelope[LearningSessionOut]:
    from apex.api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().get_session(session_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if session is None:
        raise HTTPException(status_code=404, detail=f"session d'apprentissage « {session_id} » introuvable")
    return DataEnvelope(status="real", data=_to_session_out(session))


@router.post("/{session_id}/resume", response_model=DataEnvelope[LearningSessionOut])
def resume_learning_session(session_id: str) -> DataEnvelope[LearningSessionOut]:
    from apex.errors import LearningSessionAlreadyRunning

    from apex.api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().resume_session(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LearningSessionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_session_out(session))


@router.post("/{session_id}/cancel", response_model=DataEnvelope[LearningSessionOut])
def cancel_learning_session(session_id: str) -> DataEnvelope[LearningSessionOut]:
    """No-op (retourne l'état courant tel quel) si la session n'est pas
    `running` — jamais une erreur pour un appel redondant. Une génération
    déjà en cours va toujours à son terme (annulation vérifiée ENTRE deux
    générations, jamais en cours d'une — voir `LearningOrchestrator`)."""
    from apex.api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().cancel_session(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_session_out(session))
