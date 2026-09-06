"""`GET /capabilities/{specialty}`, `GET /capabilities/{specialty}/history`,
`GET /capabilities/{specialty}/latest`, `GET /curriculum/{specialty}/
challenges`, `GET /bugs/{specialty}`, `GET /learning-sessions/{id}/
capability`, `GET /population/{specialty}/capability` — passe
« Adversarial Curriculum + Capability Benchmarking » (N+10, §18).

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.capability_benchmark_service.CapabilityBenchmarkService`/
`apex.metalearner.challenge_repository.CurriculumChallengeRepository`/
`apex.metalearner.bug_farm_service.BugFarmService` (lecture seule — le
benchmark de capacité s'exécute exclusivement à l'intérieur
d'`apex.metalearner.evolution.PopulationEvolutionService`, jamais
déclenché depuis l'API) — même patron que `apex.api.routes.
learning_feedback`.

Surface volontairement MINIMALE (§18 : lecture seule) — sept routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import (
    BugRecordOut,
    CapabilityBenchmarkRunOut,
    CapabilityDeltaOut,
    CapabilityMeasurementOut,
    CapabilityStatusOut,
    CurriculumChallengeOut,
    DataEnvelope,
)

if TYPE_CHECKING:
    from apex.metalearner.bug_farm_model import BugRecord
    from apex.metalearner.capability_model import CapabilityBenchmarkRun, CapabilityDelta
    from apex.metalearner.challenge_model import CurriculumChallenge

router = APIRouter(tags=["capability"])


def _to_measurement_out(measurement: object) -> CapabilityMeasurementOut:
    return CapabilityMeasurementOut(
        dimension=measurement.dimension.value, decision=measurement.decision.value,  # type: ignore[attr-defined]
        score=measurement.score, confidence=measurement.confidence,  # type: ignore[attr-defined]
        evidence_reference=measurement.evidence_reference,  # type: ignore[attr-defined]
    )


def _to_run_out(run: CapabilityBenchmarkRun) -> CapabilityBenchmarkRunOut:
    return CapabilityBenchmarkRunOut(
        id=run.id, specialty=run.specialty, member_id=run.member_id, generation=run.generation,
        genome_version_id=run.genome_version_id, benchmark_version=run.benchmark_version,
        gauntlet_result_id=run.gauntlet_result_id, overall_decision=run.overall_decision.value,
        overall_score=run.overall_score, overall_confidence=run.overall_confidence,
        measurements=[_to_measurement_out(m) for m in run.measurements],
        compared_champion_id=run.compared_champion_id, created_at=run.created_at.isoformat(),
    )


def _to_delta_out(delta: CapabilityDelta) -> CapabilityDeltaOut:
    return CapabilityDeltaOut(
        specialty=delta.specialty, candidate_run_id=delta.candidate_run_id,
        reference_kind=delta.reference_kind,
        reference_run_id=delta.reference_run_id,
        dimension_deltas={dim.value: value for dim, value in delta.dimension_deltas.items()},
        critical_regression=delta.critical_regression,
        regressed_dimensions=[d.value for d in delta.regressed_dimensions],
        missing_reference_dimensions=[d.value for d in delta.missing_reference_dimensions],
    )


def _to_challenge_out(challenge: CurriculumChallenge) -> CurriculumChallengeOut:
    return CurriculumChallengeOut(
        id=challenge.id, specialty=challenge.specialty, weakness_id=challenge.weakness_id,
        dimension=challenge.dimension.value, difficulty=challenge.difficulty,
        generation=challenge.generation,
        task_id=challenge.task_id, evaluation_criteria=challenge.evaluation_criteria,
        source=challenge.source.value, heldout=challenge.heldout, adversarial=challenge.adversarial,
        protocol_version=challenge.protocol_version, created_at=challenge.created_at.isoformat(),
    )


def _to_bug_out(bug: BugRecord) -> BugRecordOut:
    return BugRecordOut(
        id=bug.id, specialty=bug.specialty, weakness_id=bug.weakness_id, dimension=bug.dimension.value,
        failure_category=bug.failure_category, severity=bug.severity.value,
        first_seen_generation=bug.first_seen_generation, first_seen_at=bug.first_seen_at.isoformat(),
        member_id=bug.member_id, genome_version_id=bug.genome_version_id,
        evidence_reference=bug.evidence_reference, status=bug.status.value,
        fixed_at_generation=bug.fixed_at_generation, regressed_at_generation=bug.regressed_at_generation,
        created_at=bug.created_at.isoformat(), updated_at=bug.updated_at.isoformat(),
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


def _capability_status(specialty: str) -> CapabilityStatusOut:
    from apex.agents.runtime import AgentRuntime
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService
    from apex.metalearner.population_service import PopulationService

    session_factory = get_session_factory()
    champion = PopulationService(session_factory).get_champion(specialty)
    if champion is None:
        return CapabilityStatusOut(
            specialty=specialty, champion_member_id=None, champion_run=None, delta_vs_historical_best=None,
        )
    service = CapabilityBenchmarkService(session_factory, agent_runtime=AgentRuntime(session_factory))
    champion_run = service.get_latest(specialty, member_id=champion.id)
    delta = service.compute_delta_vs_historical_best(champion_run) if champion_run is not None else None
    return CapabilityStatusOut(
        specialty=specialty, champion_member_id=champion.id,
        champion_run=_to_run_out(champion_run) if champion_run is not None else None,
        delta_vs_historical_best=_to_delta_out(delta) if delta is not None else None,
    )


@router.get("/capabilities/{specialty}", response_model=DataEnvelope[CapabilityStatusOut])
def get_capability_status(specialty: str) -> DataEnvelope[CapabilityStatusOut]:
    _validate_specialty(specialty)
    try:
        status = _capability_status(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=status)


@router.get(
    "/capabilities/{specialty}/history", response_model=DataEnvelope[list[CapabilityBenchmarkRunOut]],
)
def get_capability_history(
    specialty: str, limit: int = 200,
) -> DataEnvelope[list[CapabilityBenchmarkRunOut]]:
    _validate_specialty(specialty)
    from apex.agents.runtime import AgentRuntime
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService

    try:
        session_factory = get_session_factory()
        service = CapabilityBenchmarkService(session_factory, agent_runtime=AgentRuntime(session_factory))
        history = service.get_history(specialty, limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_run_out(r) for r in history])


@router.get("/capabilities/{specialty}/latest", response_model=DataEnvelope[CapabilityBenchmarkRunOut])
def get_capability_latest(specialty: str) -> DataEnvelope[CapabilityBenchmarkRunOut]:
    _validate_specialty(specialty)
    from apex.agents.runtime import AgentRuntime
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService

    try:
        session_factory = get_session_factory()
        service = CapabilityBenchmarkService(session_factory, agent_runtime=AgentRuntime(session_factory))
        history = service.get_history(specialty, limit=1)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if not history:
        raise HTTPException(status_code=404, detail=f"aucun benchmark de capacité pour « {specialty} »")
    return DataEnvelope(status="real", data=_to_run_out(history[0]))


@router.get("/curriculum/{specialty}/challenges", response_model=DataEnvelope[list[CurriculumChallengeOut]])
def get_curriculum_challenges(
    specialty: str, limit: int = 200,
) -> DataEnvelope[list[CurriculumChallengeOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.challenge_repository import CurriculumChallengeRepository

    try:
        challenges = CurriculumChallengeRepository(get_session_factory()).list_by_specialty(
            specialty, limit=limit,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_challenge_out(c) for c in challenges])


@router.get("/bugs/{specialty}", response_model=DataEnvelope[list[BugRecordOut]])
def get_bugs(specialty: str, limit: int = 200) -> DataEnvelope[list[BugRecordOut]]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.bug_farm_service import BugFarmService

    try:
        bugs = BugFarmService(get_session_factory()).list_bugs(specialty, limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_bug_out(b) for b in bugs])


@router.get("/learning-sessions/{session_id}/capability", response_model=DataEnvelope[CapabilityStatusOut])
def get_learning_session_capability(session_id: str) -> DataEnvelope[CapabilityStatusOut]:
    """Délègue à la spécialité de CETTE session (`LearningSession.specialty`,
    N+7, inchangé) — jamais un second calcul, même patron qu'`apex.api.
    routes.learning_feedback.get_learning_session_weaknesses` (N+9)."""
    from apex.api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().get_session(session_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if session is None:
        raise HTTPException(status_code=404, detail=f"session d'apprentissage « {session_id} » introuvable")

    try:
        status = _capability_status(session.specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=status)


@router.get("/population/{specialty}/capability", response_model=DataEnvelope[CapabilityStatusOut])
def get_population_capability(specialty: str) -> DataEnvelope[CapabilityStatusOut]:
    """Alias intentionnel de `GET /capabilities/{specialty}` sous
    l'espace de nommage `/population` (§18 de la mission liste les deux
    séparément) — la capacité reste TOUJOURS scopée au champion courant de
    la population, jamais un second calcul."""
    _validate_specialty(specialty)
    try:
        status = _capability_status(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=status)
