"""`GET /learning-feedback/{id}`, `GET /weaknesses/{specialty}`,
`GET /weaknesses/{specialty}/history`, `GET /curriculum/{specialty}`,
`GET /population/{specialty}/learning-signals`, `GET /learning-sessions/
{id}/weaknesses` — passe « Judge Feedback -> Weakness -> Curriculum ->
Adaptive Evolution » (N+9).

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.judge_feedback_service.JudgeFeedbackService`/
`apex.metalearner.weakness_engine.WeaknessEngineService`/
`apex.metalearner.curriculum_engine.CurriculumEngineService` (lecture
seule — le Judge Feedback s'exécute exclusivement à l'intérieur
d'`apex.metalearner.evolution.PopulationEvolutionService`, jamais
déclenché depuis l'API) — même patron que `apex.api.routes.gauntlets`.

Surface volontairement MINIMALE (§12 de la mission : « Pas de mutation
API inutile ») — six routes, toutes en lecture seule."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import (
    CurriculumTargetOut,
    DataEnvelope,
    GauntletWeaknessOut,
    JudgeLearningSignalOut,
)

if TYPE_CHECKING:
    from apex.metalearner.judge_feedback_model import (
        CurriculumTarget,
        GauntletWeakness,
        JudgeLearningSignal,
    )

router = APIRouter(tags=["learning-feedback"])


def _to_signal_out(signal: JudgeLearningSignal) -> JudgeLearningSignalOut:
    return JudgeLearningSignalOut(
        id=signal.id, gauntlet_result_id=signal.gauntlet_result_id, member_id=signal.member_id,
        specialty=signal.specialty, generation=signal.generation,
        genome_version_id=signal.genome_version_id,
        judge_id=signal.judge_id, judge_version=signal.judge_version, dimension=signal.dimension.value,
        decision=signal.decision.value, category=signal.category.value,
        failure_category=signal.failure_category.value if signal.failure_category else None,
        severity=signal.severity.value if signal.severity else None, score=signal.score,
        confidence=signal.confidence, finding_summary=signal.finding_summary,
        evidence_reference=signal.evidence_reference, protocol_version=signal.protocol_version,
        created_at=signal.created_at.isoformat(),
    )


def _to_weakness_out(weakness: GauntletWeakness) -> GauntletWeaknessOut:
    return GauntletWeaknessOut(
        specialty=weakness.specialty, dimension=weakness.dimension.value,
        failure_category=weakness.failure_category.value, severity=weakness.severity.value,
        frequency=weakness.frequency, confidence=weakness.confidence,
        first_seen=weakness.first_seen.isoformat(), last_seen=weakness.last_seen.isoformat(),
        first_seen_generation=weakness.first_seen_generation,
        last_seen_generation=weakness.last_seen_generation,
        affected_candidates=list(weakness.affected_candidates),
        affected_generations=list(weakness.affected_generations),
        affected_genomes=list(weakness.affected_genomes),
        evidence_count=weakness.evidence_count, recurrence_count=weakness.recurrence_count,
        improvement_status=weakness.improvement_status.value,
        suspected_inherited_from=weakness.suspected_inherited_from,
    )


def _to_target_out(target: CurriculumTarget) -> CurriculumTargetOut:
    return CurriculumTargetOut(
        specialty=target.specialty, dimension=target.dimension.value,
        weakness=_to_weakness_out(target.weakness),
        recommended_mode=target.recommended_mode.value if target.recommended_mode else None,
        base_task_id=target.base_task_id, focus_genome_paths=list(target.focus_genome_paths),
        rationale=target.rationale,
        edge_case_kind=target.edge_case_kind.value if target.edge_case_kind else None,
        adversarial_kind=target.adversarial_kind.value if target.adversarial_kind else None,
        generalization_variant=target.generalization_variant.value if target.generalization_variant else None,
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


@router.get("/learning-feedback/{signal_id}", response_model=DataEnvelope[JudgeLearningSignalOut])
def get_learning_feedback(signal_id: str) -> DataEnvelope[JudgeLearningSignalOut]:
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.judge_feedback_service import JudgeFeedbackService

    try:
        signal = JudgeFeedbackService(get_session_factory()).get(signal_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if signal is None:
        raise HTTPException(status_code=404, detail=f"signal d'apprentissage « {signal_id} » introuvable")
    return DataEnvelope(status="real", data=_to_signal_out(signal))


@router.get("/weaknesses/{specialty}", response_model=DataEnvelope[list[GauntletWeaknessOut]])
def get_weaknesses(specialty: str) -> DataEnvelope[list[GauntletWeaknessOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.weakness_engine import WeaknessEngineService

    try:
        weaknesses = WeaknessEngineService(get_session_factory()).aggregate(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_weakness_out(w) for w in weaknesses])


@router.get("/weaknesses/{specialty}/history", response_model=DataEnvelope[list[JudgeLearningSignalOut]])
def get_weakness_history(
    specialty: str, dimension: str | None = None, limit: int = 200,
) -> DataEnvelope[list[JudgeLearningSignalOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.judge_model import JudgeDimension
    from apex.metalearner.weakness_engine import WeaknessEngineService

    try:
        parsed_dimension = JudgeDimension(dimension) if dimension is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"dimension « {dimension} » inconnue") from exc

    try:
        signals = WeaknessEngineService(get_session_factory()).history(
            specialty, dimension=parsed_dimension, limit=limit,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_signal_out(s) for s in signals])


@router.get("/curriculum/{specialty}", response_model=DataEnvelope[list[CurriculumTargetOut]])
def get_curriculum(specialty: str) -> DataEnvelope[list[CurriculumTargetOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.curriculum_engine import CurriculumEngineService

    try:
        targets = CurriculumEngineService(get_session_factory()).build_targets(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_target_out(t) for t in targets])


@router.get(
    "/population/{specialty}/learning-signals", response_model=DataEnvelope[list[JudgeLearningSignalOut]],
)
def list_learning_signals_for_specialty(
    specialty: str, member_id: str | None = None, limit: int = 500,
) -> DataEnvelope[list[JudgeLearningSignalOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.judge_feedback_service import JudgeFeedbackService

    try:
        service = JudgeFeedbackService(get_session_factory())
        signals = (
            service.list_by_member(member_id, limit=limit) if member_id is not None
            else service.list_by_specialty(specialty, limit=limit)
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_signal_out(s) for s in signals])


@router.get(
    "/learning-sessions/{session_id}/weaknesses", response_model=DataEnvelope[list[GauntletWeaknessOut]],
)
def get_learning_session_weaknesses(session_id: str) -> DataEnvelope[list[GauntletWeaknessOut]]:
    """Délègue à `GET /weaknesses/{specialty}` pour la spécialité de CETTE
    session (`LearningSession.specialty`, N+7, inchangé) — jamais un
    second calcul de faiblesse."""
    from apex.api.dependencies import get_session_factory
    from apex.research_api.dependencies import get_learning_orchestrator
    from apex.metalearner.weakness_engine import WeaknessEngineService

    try:
        session = get_learning_orchestrator().get_session(session_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if session is None:
        raise HTTPException(status_code=404, detail=f"session d'apprentissage « {session_id} » introuvable")

    try:
        weaknesses = WeaknessEngineService(get_session_factory()).aggregate(session.specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_weakness_out(w) for w in weaknesses])
