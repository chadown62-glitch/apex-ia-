"""`GET /capabilities/{specialty}`, `GET /capabilities/{specialty}/history`,
`GET /capabilities/{specialty}/latest`, `GET /curriculum/{specialty}/
challenges`, `GET /bugs/{specialty}`, `GET /learning-sessions/{id}/
capability`, `GET /population/{specialty}/capability` — passe
« Adversarial Curriculum + Capability Benchmarking » (N+10, §18).

**N+11 (« Universal Task + Curriculum Engine », § Phase 20)** — sept routes
supplémentaires, TOUJOURS lecture seule : `GET /curriculum/{specialty}/
capabilities` (matrice `SpecialtyTaskProvider`), `GET /curriculum/
{specialty}/plan` (`CurriculumPlan`), `GET /curriculum/{specialty}/tasks`
(tâches RÉELLEMENT référencées par le curriculum de cette spécialité),
`GET /curriculum/{specialty}/history` (alias de `/challenges` — l'historique
DU curriculum EST son propre journal de challenges persistés, aucune
seconde source), `GET /tasks/{task_id}/provenance`, `GET /tasks/{task_id}/
variants`, `GET /bug-farm/{specialty}` (alias de `/bugs/{specialty}`),
`GET /specialties`.

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.capability_benchmark_service.CapabilityBenchmarkService`/
`apex.metalearner.challenge_repository.CurriculumChallengeRepository`/
`apex.metalearner.bug_farm_service.BugFarmService`/`apex.metalearner.
curriculum_engine.CurriculumEngineService`/`apex.metalearner.
provenance_service.TaskProvenanceService`/`apex.taskgen.registry` (lecture
seule — le benchmark de capacité et le curriculum s'exécutent
exclusivement à l'intérieur d'`apex.metalearner.evolution.
PopulationEvolutionService`, jamais déclenchés depuis l'API) — même patron
que `apex.api.routes.learning_feedback`."""

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
    CurriculumPlanItemOut,
    CurriculumPlanOut,
    DataEnvelope,
    SpecialtyCapabilityMatrixOut,
    SpecialtyOut,
    TaskOut,
    TaskProvenanceOut,
)

if TYPE_CHECKING:
    from apex.metalearner.bug_farm_model import BugRecord
    from apex.metalearner.capability_model import CapabilityBenchmarkRun, CapabilityDelta
    from apex.metalearner.challenge_model import CurriculumChallenge
    from apex.metalearner.curriculum_plan import CurriculumPlan
    from apex.specialties.model import Specialty
    from apex.taskgen.model import TaskProvenance
    from apex.tasks.model import Task

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
        protocol_version=challenge.protocol_version, parent_task_id=challenge.parent_task_id,
        seed=challenge.seed, created_at=challenge.created_at.isoformat(),
    )


def _to_plan_out(plan: CurriculumPlan) -> CurriculumPlanOut:
    return CurriculumPlanOut(
        specialty=plan.specialty, generation=plan.generation,
        items=[
            CurriculumPlanItemOut(
                kind=item.kind.value, dimension=item.dimension.value if item.dimension else None,
                count=item.count, priority=item.priority, difficulty=int(item.difficulty),
            )
            for item in plan.items
        ],
        total_task_count=plan.total_task_count, truncated=plan.truncated,
        protocol_version=plan.protocol_version,
    )


def _to_task_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id, split=task.split.value, specialty=task.specialty, title=task.title,
        description=task.description, command=list(task.command), dataset_ref=task.dataset_ref,
        created_by=task.created_by, expected_answer=task.expected_answer,
        created_at=task.created_at.isoformat(),
    )


def _to_provenance_out(task_id: str, provenance: TaskProvenance) -> TaskProvenanceOut:
    return TaskProvenanceOut(
        task_id=task_id, weakness_id=provenance.weakness_id, parent_task_id=provenance.parent_task_id,
        challenge_id=provenance.challenge_id, bug_record_id=provenance.bug_record_id,
        benchmark_id=provenance.benchmark_id, generator_version=provenance.generator_version,
        seed=provenance.seed,
    )


def _to_specialty_out(specialty: Specialty) -> SpecialtyOut:
    return SpecialtyOut(
        id=specialty.id, name=specialty.name, description=specialty.description,
        dev_stage=specialty.dev_stage.value, is_supported=specialty.is_supported,
        capabilities=list(specialty.capabilities), tools=list(specialty.tools),
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


def _bugs_for(specialty: str, limit: int) -> DataEnvelope[list[BugRecordOut]]:
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.bug_farm_service import BugFarmService

    try:
        bugs = BugFarmService(get_session_factory()).list_bugs(specialty, limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_bug_out(b) for b in bugs])


@router.get("/bugs/{specialty}", response_model=DataEnvelope[list[BugRecordOut]])
def get_bugs(specialty: str, limit: int = 200) -> DataEnvelope[list[BugRecordOut]]:
    _validate_specialty(specialty)
    return _bugs_for(specialty, limit)


@router.get("/bug-farm/{specialty}", response_model=DataEnvelope[list[BugRecordOut]])
def get_bug_farm(specialty: str, limit: int = 200) -> DataEnvelope[list[BugRecordOut]]:
    """Alias de `GET /bugs/{specialty}` sous l'espace de nommage
    `/bug-farm` (§20 de la mission N+11 liste les deux séparément) — même
    lecture, jamais un second calcul."""
    _validate_specialty(specialty)
    return _bugs_for(specialty, limit)


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


# ---------------------------------------------------------------------------
# N+11 — « Universal Task + Curriculum Engine » (§ Phase 20 de la mission).
# ---------------------------------------------------------------------------


@router.get(
    "/curriculum/{specialty}/capabilities", response_model=DataEnvelope[SpecialtyCapabilityMatrixOut],
)
def get_curriculum_capabilities(specialty: str) -> DataEnvelope[SpecialtyCapabilityMatrixOut]:
    """§ Phase 5 : matrice SUPPORTED/UNSUPPORTED du `SpecialtyTaskProvider`
    RÉEL de `specialty` (`apex.taskgen.registry`) — `supported=False` +
    matrice vide quand aucun provider n'est enregistré, jamais une capacité
    prétendue."""
    _validate_specialty(specialty)
    from apex.taskgen.registry import get_provider

    try:
        provider = get_provider(specialty)
        if provider is None:
            return DataEnvelope(
                status="real",
                data=SpecialtyCapabilityMatrixOut(specialty=specialty, supported=False, capabilities={}),
            )
        matrix = {kind.value: supported for kind, supported in provider.capability_matrix().items()}
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    data = SpecialtyCapabilityMatrixOut(specialty=specialty, supported=True, capabilities=matrix)
    return DataEnvelope(status="real", data=data)


@router.get("/curriculum/{specialty}/plan", response_model=DataEnvelope[CurriculumPlanOut])
def get_curriculum_plan(specialty: str, generation: int = 0) -> DataEnvelope[CurriculumPlanOut]:
    """§ Phase 13/14 — `CurriculumEngineService.build_plan`, borné par la
    `CurriculumBudgetPolicy` RÉELLEMENT chargée (`apex.config.loader.
    load_config`), jamais une constante de code."""
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.config.loader import load_config
    from apex.metalearner.curriculum_engine import CurriculumEngineService

    try:
        budget = load_config().curriculum_budget
        service = CurriculumEngineService(get_session_factory())
        plan = service.build_plan(specialty, generation, budget=budget)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_plan_out(plan))


@router.get("/curriculum/{specialty}/tasks", response_model=DataEnvelope[list[TaskOut]])
def get_curriculum_tasks(specialty: str, limit: int = 200) -> DataEnvelope[list[TaskOut]]:
    """Tâches RÉELLEMENT référencées par le curriculum de `specialty`
    (`CurriculumChallenge.task_id`, jamais toutes les `Task` de la
    spécialité — celles écrites à la main en sont exclues)."""
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.challenge_repository import CurriculumChallengeRepository
    from apex.tasks.repository import TaskRepository

    try:
        session_factory = get_session_factory()
        challenge_repo = CurriculumChallengeRepository(session_factory)
        challenges = challenge_repo.list_by_specialty(specialty, limit=limit)
        task_repo = TaskRepository(session_factory)
        seen: set[str] = set()
        tasks: list[Task] = []
        for challenge in challenges:
            if challenge.task_id in seen:
                continue
            seen.add(challenge.task_id)
            task = task_repo.get(challenge.task_id)
            if task is not None:
                tasks.append(task)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_task_out(t) for t in tasks])


@router.get(
    "/curriculum/{specialty}/history", response_model=DataEnvelope[list[CurriculumChallengeOut]],
)
def get_curriculum_history(specialty: str, limit: int = 200) -> DataEnvelope[list[CurriculumChallengeOut]]:
    """Alias de `GET /curriculum/{specialty}/challenges` — l'historique DU
    curriculum EST son propre journal de challenges INSERT-seul persistés
    (`curriculum_challenges`), jamais une seconde source."""
    return get_curriculum_challenges(specialty, limit)


@router.get("/tasks/{task_id}/provenance", response_model=DataEnvelope[TaskProvenanceOut])
def get_task_provenance(task_id: str) -> DataEnvelope[TaskProvenanceOut]:
    """§ Phase 16 — reconstruit la provenance depuis le `CurriculumChallenge`
    référençant `task_id` (`apex.metalearner.provenance_service.
    TaskProvenanceService`) — `404` si `task_id` n'a jamais été référencé
    par le curriculum (tâche écrite à la main, provenance non applicable)."""
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.provenance_service import TaskProvenanceService

    try:
        provenance = TaskProvenanceService(get_session_factory()).get_provenance(task_id)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    if provenance is None:
        detail = f"aucune provenance curriculum pour la tâche « {task_id} »"
        raise HTTPException(status_code=404, detail=detail)
    return DataEnvelope(status="real", data=_to_provenance_out(task_id, provenance))


@router.get("/tasks/{task_id}/variants", response_model=DataEnvelope[list[CurriculumChallengeOut]])
def get_task_variants(task_id: str, limit: int = 200) -> DataEnvelope[list[CurriculumChallengeOut]]:
    """§ Phase 6/20 — tous les `CurriculumChallenge` dont `task_id` est la
    tâche PARENTE (`apex.taskgen.variant_engine.VariantEngine`), jamais une
    liste devinée."""
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.provenance_service import TaskProvenanceService

    try:
        variants = TaskProvenanceService(get_session_factory()).get_variants(task_id, limit=limit)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_to_challenge_out(c) for c in variants])


@router.get("/specialties", response_model=DataEnvelope[list[SpecialtyOut]])
def get_specialties() -> DataEnvelope[list[SpecialtyOut]]:
    """Miroir READ-ONLY d'`apex.specialties.registry.SPECIALTIES` — jamais
    une écriture, cette route n'existait pas avant N+11 (§20 de la
    mission)."""
    from apex.specialties.registry import SPECIALTIES

    return DataEnvelope(status="real", data=[_to_specialty_out(s) for s in SPECIALTIES])
