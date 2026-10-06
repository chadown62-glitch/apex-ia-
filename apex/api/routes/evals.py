"""GET /tasks, GET /eval-runs, GET /weakness/{specialty},
GET /self-model/{specialty}, POST /experiment-cycles — passe « API
Évaluation/Apprentissage ».

Ce routeur ne réimplémente aucun moteur métier : il ne fait que composer
les couches domaine déjà réelles et testées depuis les phases 5/12/18/22 —
`apex.tasks.repository.TaskRepository`, `apex.evalrunner.repository.
EvalRunRepository`, `apex.weakness.service.WeaknessDetectorService`,
`apex.selfmodel.view.get_self_model_view`, `apex.metalearner.cycle.
run_experiment_cycle`. Aucune nouvelle table, aucune nouvelle migration,
aucun second système d'autorisation : le jeton (`apex.api.auth`) reste la
seule frontière d'authentification, appliquée uniformément par la boucle
d'inclusion d'`apex.api.app` ; `POST /experiment-cycles` appelle
systématiquement l'orchestrateur avec `Subject.HUMAN` — même discipline que
`create_mission`/`create_project`/`create_task` (aucun champ d'identité
fourni par l'appelant HTTP n'est jamais transmis à `PolicyEngine`).

Distinction d'erreurs volontaire :
    - DB/infrastructure injoignable -> `DataEnvelope(status="unknown")`, 200
      — même convention que CHAQUE route existante de cette API (`/audit`,
      `/agents`, `/configured-agents`, ...) : un `except Exception` large
      est déjà le patron établi pour ce cas précis, jamais pour masquer un
      bug — le détail de l'exception reste toujours visible dans la
      réponse, jamais avalé silencieusement. Le check « ressource absente »
      (404, ci-dessous) reste TOUJOURS placé hors de ce bloc `try`, pour ne
      jamais être confondu avec une infrastructure absente.
    - ressource introuvable après une lecture réussie -> 404.
    - entrée invalide (spécialité inconnue, niveau/méthode/split invalide,
      clone introuvable) -> 422, vérifié explicitement AVANT le `except`
      générique — jamais absorbé dedans.
    - refus `PolicyEngine` (défense en profondeur — cette route ne peut
      structurellement pas le déclencher puisqu'elle force toujours
      `Subject.HUMAN`, mais `run_experiment_cycle` reste protégé si un
      appelant interne futur passait un autre sujet) -> 403, vérifié
      explicitement avant le `except` générique lui aussi.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from apex.api.schemas import (
    CalibratedConfidenceOut,
    CurriculumDecisionOut,
    DataEnvelope,
    EvalRunOut,
    ExperimentCycleCreateIn,
    ExperimentCycleOut,
    MeasurementOut,
    ResourceCostOut,
    SelfModelSpecialtyOut,
    SkillOut,
    StrategyDecisionOut,
    TaskOut,
    WeaknessReportOut,
)

router = APIRouter(tags=["evals"])


def _to_task_out(task: Any) -> TaskOut:
    return TaskOut(
        id=task.id, split=task.split.value, specialty=task.specialty, title=task.title,
        description=task.description, command=list(task.command), dataset_ref=task.dataset_ref,
        created_by=task.created_by, expected_answer=task.expected_answer,
        created_at=task.created_at.isoformat(),
    )


def _to_eval_run_out(run: Any) -> EvalRunOut:
    return EvalRunOut(
        id=run.id, task_id=run.task_id, method=run.method.value,
        oracle_verdict=run.oracle_verdict.value, oracle_detail=run.oracle_detail,
        exit_code=run.exit_code, elapsed_s=run.elapsed_s, facts=run.facts, created_by=run.created_by,
        clone_id=run.clone_id, sandbox_status=run.sandbox_status.value if run.sandbox_status else None,
        model_id=run.model_id, seed=run.seed, dataset_ref=run.dataset_ref,
        experiment_ref=run.experiment_ref,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        created_at=run.created_at.isoformat(),
    )


def _to_weakness_out(report: Any) -> WeaknessReportOut:
    return WeaknessReportOut(
        category=report.category.value, pass_count=report.pass_count, fail_count=report.fail_count,
        error_count=report.error_count, timeout_count=report.timeout_count,
        unknown_count=report.unknown_count, failure_rate=report.failure_rate,
        evidence_run_ids=list(report.evidence_run_ids), reason=report.reason,
    )


def _to_skill_out(skill: Any) -> SkillOut:
    return SkillOut(
        id=skill.id, name=skill.name, specialty=skill.specialty, description=skill.description,
        status=skill.status.value, prerequisite_skill_ids=list(skill.prerequisite_skill_ids),
        evidence_run_ids=list(skill.evidence_run_ids), confidence=skill.confidence,
        supersedes_id=skill.supersedes_id, created_by=skill.created_by,
        created_at=skill.created_at.isoformat(), decided_by=skill.decided_by,
        decided_at=skill.decided_at.isoformat() if skill.decided_at else None,
        decision_reason=skill.decision_reason,
    )


def _to_calibrated_confidence_out(confidence: Any) -> CalibratedConfidenceOut:
    return CalibratedConfidenceOut(
        status=confidence.status.value, sample_size=confidence.sample_size,
        observed_success_rate=confidence.observed_success_rate,
        calibrated_point_estimate=confidence.calibrated_point_estimate,
        calibrated_lower_bound=confidence.calibrated_lower_bound,
        calibrated_upper_bound=confidence.calibrated_upper_bound,
        declared_confidence=confidence.declared_confidence,
        calibration_gap=confidence.calibration_gap, reason=confidence.reason,
    )


def _to_resource_cost_out(cost: Any) -> ResourceCostOut:
    def _measurement(m: Any) -> MeasurementOut:
        return MeasurementOut(value=m.value, status=m.status.value)

    return ResourceCostOut(
        elapsed_s=_measurement(cost.elapsed_s), cpu_time_s=_measurement(cost.cpu_time_s),
        memory_peak_mb=_measurement(cost.memory_peak_mb), token_count=_measurement(cost.token_count),
        experiment_count=cost.experiment_count,
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


# -- tasks --------------------------------------------------------------------


@router.get("/tasks", response_model=DataEnvelope[list[TaskOut]])
def list_tasks(
    specialty: str | None = Query(default=None),
    split: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> DataEnvelope[list[TaskOut]]:
    from apex.api.dependencies import get_session_factory
    from apex.tasks.model import TaskSplit
    from apex.tasks.repository import TaskRepository

    if split is not None:
        try:
            TaskSplit(split)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"split « {split} » inconnu") from exc
    if specialty is not None:
        _validate_specialty(specialty)

    try:
        repo = TaskRepository(get_session_factory())
        if specialty is not None:
            tasks = repo.list_by_specialty(specialty, limit=limit)
        elif split is not None:
            tasks = repo.list_by_split(split, limit=limit)
        else:
            tasks = repo.list_all(limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_task_out(t) for t in tasks])


@router.get("/tasks/{task_id}", response_model=DataEnvelope[TaskOut])
def get_task(task_id: str) -> DataEnvelope[TaskOut]:
    from apex.api.dependencies import get_session_factory
    from apex.tasks.repository import TaskRepository

    try:
        task = TaskRepository(get_session_factory()).get(task_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if task is None:
        raise HTTPException(status_code=404, detail=f"tâche « {task_id} » introuvable")
    return DataEnvelope(status="real", data=_to_task_out(task))


# -- eval-runs ------------------------------------------------------------


@router.get("/eval-runs", response_model=DataEnvelope[list[EvalRunOut]])
def list_eval_runs(
    task_id: str | None = Query(default=None),
    clone_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> DataEnvelope[list[EvalRunOut]]:
    from apex.api.dependencies import get_session_factory
    from apex.evalrunner.repository import EvalRunRepository

    try:
        repo = EvalRunRepository(get_session_factory())
        if task_id is not None:
            runs = repo.list_by_task(task_id, limit=limit)
        elif clone_id is not None:
            runs = repo.list_by_clone(clone_id, limit=limit)
        else:
            runs = repo.list_all(limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_eval_run_out(r) for r in runs])


@router.get("/eval-runs/{eval_run_id}", response_model=DataEnvelope[EvalRunOut])
def get_eval_run(eval_run_id: str) -> DataEnvelope[EvalRunOut]:
    from apex.api.dependencies import get_session_factory
    from apex.evalrunner.repository import EvalRunRepository

    try:
        run = EvalRunRepository(get_session_factory()).get(eval_run_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if run is None:
        raise HTTPException(status_code=404, detail=f"eval-run « {eval_run_id} » introuvable")
    return DataEnvelope(status="real", data=_to_eval_run_out(run))


# -- weakness ---------------------------------------------------------------
