"""Routes de RECHERCHE retirées de `apex.api.routes.evals` (06/10/2026) : faiblesses, auto-modèle, cycles
d'expérience. Elles composent `apex.weakness`, `apex.selfmodel` et `apex.metalearner` (paquets de recherche, absents
du Runtime livré) ; montées par l'extension `apex.research_api` seulement quand elle est présente. Les helpers de
conversion restent dans `apex.api.routes.evals` (runtime) et sont réutilisés tels quels."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apex.api.routes.evals import (
    _to_calibrated_confidence_out,
    _to_eval_run_out,
    _to_resource_cost_out,
    _to_skill_out,
    _to_task_out,
    _to_weakness_out,
    _validate_specialty,
)
from apex.api.schemas import (
    CurriculumDecisionOut,
    DataEnvelope,
    ExperimentCycleCreateIn,
    ExperimentCycleOut,
    SelfModelSpecialtyOut,
    StrategyDecisionOut,
    WeaknessReportOut,
)

router = APIRouter(tags=["evals"])


@router.get("/weakness/{specialty}", response_model=DataEnvelope[WeaknessReportOut])
def get_weakness(specialty: str) -> DataEnvelope[WeaknessReportOut]:
    _validate_specialty(specialty)

    from apex.api.dependencies import get_session_factory
    from apex.weakness.service import WeaknessDetectorService

    try:
        report = WeaknessDetectorService(get_session_factory()).detect_for_specialty(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_weakness_out(report))


# -- self-model ---------------------------------------------------------------


@router.get("/self-model/{specialty}", response_model=DataEnvelope[SelfModelSpecialtyOut])
def get_self_model(specialty: str) -> DataEnvelope[SelfModelSpecialtyOut]:
    _validate_specialty(specialty)

    from apex.api.dependencies import get_session_factory
    from apex.selfmodel.view import get_self_model_view

    try:
        view = get_self_model_view(get_session_factory(), specialties=[specialty])
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    knowledge = view.specialties[0]
    out = SelfModelSpecialtyOut(
        specialty=knowledge.specialty,
        acquired_skills=[_to_skill_out(s) for s in knowledge.acquired_skills],
        proposed_skills=[_to_skill_out(s) for s in knowledge.proposed_skills],
        weakness_report=_to_weakness_out(knowledge.weakness_report),
        calibrated_confidence=_to_calibrated_confidence_out(knowledge.calibrated_confidence),
        eval_run_count=knowledge.eval_run_count,
        methods_used=[m.value for m in knowledge.methods_used],
        genome_refs_used=list(knowledge.genome_refs_used),
        cost=_to_resource_cost_out(knowledge.cost),
        last_skill_validated_at=(
            knowledge.last_skill_validated_at.isoformat() if knowledge.last_skill_validated_at else None
        ),
    )
    return DataEnvelope(status="real", data=out)


# -- experiment-cycles (seul point d'écriture de cette passe) -----------------


@router.post("/experiment-cycles", response_model=DataEnvelope[ExperimentCycleOut])
def create_experiment_cycle(payload: ExperimentCycleCreateIn) -> DataEnvelope[ExperimentCycleOut]:
    """Appelle réellement `apex.metalearner.cycle.run_experiment_cycle` —
    aucune simulation, aucun `EvalRun` fabriqué. `requested_by` est TOUJOURS
    `Subject.HUMAN`, jamais un champ fourni par l'appelant HTTP (même
    discipline que `create_task`/`create_mission`/`create_project`) : le
    jeton API prouve seulement que l'appelant connaît le secret local,
    jamais une identité — voir `apex/api/auth.py`."""
    _validate_specialty(payload.specialty)

    from apex.agents.runtime import AgentRuntime
    from apex.api.dependencies import get_session_factory
    from apex.errors import NotImplementedYet, PolicyDenied
    from apex.evalrunner.model import EvalMethod
    from apex.metalearner.cycle import run_experiment_cycle
    from apex.policy.model import Subject
    from apex.tasks.model import TaskSplit

    try:
        method = EvalMethod(payload.method)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"méthode « {payload.method} » inconnue") from exc
    try:
        split = TaskSplit(payload.split)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"split « {payload.split} » inconnu") from exc

    try:
        session_factory = get_session_factory()
        result = run_experiment_cycle(
            session_factory=session_factory,
            agent_runtime=AgentRuntime(session_factory),
            clone_id=payload.clone_id,
            specialty=payload.specialty,
            current_level=payload.current_level,
            requested_by=Subject.HUMAN,
            method=method,
            split=split,
        )
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, NotImplementedYet, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")

    out = ExperimentCycleOut(
        decision=StrategyDecisionOut(
            specialty=result.decision.specialty,
            mode=result.decision.mode.value if result.decision.mode else None,
            level=result.decision.level,
            curriculum_decision=CurriculumDecisionOut(
                current_level=result.decision.curriculum_decision.current_level,
                recommended_level=result.decision.curriculum_decision.recommended_level,
                direction=result.decision.curriculum_decision.direction.value,
                reason=result.decision.curriculum_decision.reason,
            ),
            reason=result.decision.reason,
        ),
        task=_to_task_out(result.task),
        eval_run=_to_eval_run_out(result.eval_run),
        weakness_before=_to_weakness_out(result.weakness_before),
        weakness_after=_to_weakness_out(result.weakness_after),
    )
    return DataEnvelope(status="real", data=out)
