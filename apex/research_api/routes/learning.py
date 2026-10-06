"""POST /learning/cycles, GET /learning/status, GET /learning/history,
POST /learning/cycles/cancel — passe « Autonomous Learning Trigger +
Scheduler/CLI Execution Path ».

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.autonomy.AutonomousLearningService` (déjà réel, orchestre
`run_experiment_cycle` sans le dupliquer) et
`apex.metalearner.autonomous_scheduler.AutonomousLearningScheduler` (état
`enabled`/`is_running` uniquement — ce routeur ne démarre JAMAIS le thread
du Scheduler lui-même, voir `apex.api.dependencies.get_learning_scheduler`).

**Sécurité — identique à `POST /experiment-cycles`** (`apex/api/routes/
evals.py`) : `POST /learning/cycles` ne prend AUCUN champ d'identité
(`subject`/`role`/`actor`/`requested_by`) dans son corps — `apex.metalearner.
autonomy.AutonomousLearningService.start_cycle` force `Subject.HUMAN` en
dur, jamais négociable depuis l'extérieur. Le jeton API (`apex.api.auth.
require_api_token`) prouve seulement que l'appelant connaît le secret local,
même discipline que partout ailleurs dans cette API.

Distinction d'erreurs volontaire, même patron qu'`evals.py` :
    - DB/infrastructure injoignable -> `DataEnvelope(status="unknown")`, 200
      (`except Exception` large, même convention établie que chaque route
      existante de cette API).
    - entrée invalide (spécialité inconnue, méthode/split invalide) -> 422,
      vérifié explicitement AVANT le `except` générique.
    - un cycle est déjà `RUNNING` (`CycleAlreadyRunning`) -> 409, vérifié
      explicitement avant le `except` générique aussi — jamais un second
      cycle silencieusement concurrent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import (
    AutonomousLearningStatusOut,
    DataEnvelope,
    LearningCycleHistoryEntryOut,
    LearningCycleStartIn,
    LearningCycleStatusOut,
)

# Différé (voir la docstring d'`apex.api.dependencies` et le patron déjà
# établi par `apex.api.routes.evals` : `apex.metalearner` n'est importé nulle
# part au niveau module par les routes existantes, uniquement dans le corps
# des fonctions) — `apex.api.app` doit rester important sans que
# `apex.metalearner` fasse nécessairement partie de l'environnement/artefact
# packagé (voir `tests/test_runtime_packaging.py`, hors périmètre de cette
# passe, jamais modifié pour cette raison).
if TYPE_CHECKING:
    from apex.metalearner.autonomy import CycleHistoryEntry, CycleStatus

router = APIRouter(prefix="/learning", tags=["learning"])


def _to_status_out(status: CycleStatus) -> LearningCycleStatusOut:
    return LearningCycleStatusOut(
        state=status.state.value, cycle_id=status.cycle_id, specialty=status.specialty,
        started_at=status.started_at.isoformat() if status.started_at else None,
        completed_at=status.completed_at.isoformat() if status.completed_at else None,
        error=status.error, eval_run_id=status.eval_run_id, task_id=status.task_id,
        oracle_verdict=status.oracle_verdict, skill_candidate_id=status.skill_candidate_id,
        genome_candidate_id=status.genome_candidate_id,
    )


def _to_history_entry_out(entry: CycleHistoryEntry) -> LearningCycleHistoryEntryOut:
    return LearningCycleHistoryEntryOut(
        cycle_id=entry.cycle_id, specialty=entry.specialty, state=entry.state.value,
        eval_run_id=entry.eval_run_id, task_id=entry.task_id, oracle_verdict=entry.oracle_verdict,
        error=entry.error, completed_at=entry.completed_at.isoformat(),
        skill_candidate_id=entry.skill_candidate_id, genome_candidate_id=entry.genome_candidate_id,
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


@router.post("/cycles", response_model=DataEnvelope[LearningCycleStatusOut])
def start_learning_cycle(payload: LearningCycleStartIn) -> DataEnvelope[LearningCycleStatusOut]:
    """Démarre un cycle d'apprentissage autonome en tâche de fond et
    retourne IMMÉDIATEMENT son état `RUNNING` — jamais bloquant (contraste
    volontaire avec `POST /experiment-cycles`, synchrone et humain-direct ;
    voir la docstring d'`apex.metalearner.autonomy` pour pourquoi ces deux
    routes ne partagent délibérément pas le même mécanisme)."""
    _validate_specialty(payload.specialty)

    from apex.errors import CycleAlreadyRunning
    from apex.evalrunner.model import EvalMethod
    from apex.tasks.model import TaskSplit

    try:
        method = EvalMethod(payload.method)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"méthode « {payload.method} » inconnue") from exc
    try:
        split = TaskSplit(payload.split)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"split « {payload.split} » inconnu") from exc

    from apex.research_api.dependencies import get_learning_service

    try:
        status = get_learning_service().start_cycle(
            specialty=payload.specialty, current_level=payload.current_level,
            method=method, split=split, clone_id=payload.clone_id,
        )
    except CycleAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.post("/cycles/cancel", response_model=DataEnvelope[LearningCycleStatusOut])
def cancel_learning_cycle() -> DataEnvelope[LearningCycleStatusOut]:
    """No-op (retourne l'état courant tel quel) si aucun cycle n'est
    `RUNNING` — jamais une erreur pour un appel redondant."""
    from apex.research_api.dependencies import get_learning_service

    try:
        status = get_learning_service().cancel()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_status_out(status))


@router.get("/status", response_model=DataEnvelope[AutonomousLearningStatusOut])
def get_learning_status() -> DataEnvelope[AutonomousLearningStatusOut]:
    from apex.research_api.dependencies import get_learning_scheduler, get_learning_service

    try:
        service = get_learning_service()
        scheduler = get_learning_scheduler()
        data = AutonomousLearningStatusOut(
            enabled=scheduler.enabled, scheduler_running=scheduler.is_running,
            interval_s=scheduler.interval_s, cycle=_to_status_out(service.status()),
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)


@router.get("/history", response_model=DataEnvelope[list[LearningCycleHistoryEntryOut]])
def get_learning_history(limit: int = 20) -> DataEnvelope[list[LearningCycleHistoryEntryOut]]:
    from apex.research_api.dependencies import get_learning_service

    try:
        entries = get_learning_service().history(limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_history_entry_out(e) for e in entries])
