"""`apex.selfmodel` — la vue « Que sait APEX ? », composée à la lecture.

PAS une nouvelle abstraction/entrée de registre (même raisonnement que
`apex.metalearner.cycle.run_experiment_cycle`, déjà jugé composant plutôt
que nouvelle brique) : un module de COMPOSITION READ-ONLY, jamais une
table, jamais un `SelfModelManager` — même patron qu'`apex.agents.view`
(déjà établi) : une vue est une fonction qui assemble des lectures réelles,
pas une classe qui retient un état entre deux appels.

Compose EXCLUSIVEMENT des lectures déjà réelles :
    SkillRegistryService (Phase 14)       -- compétences connues/proposées
    WeaknessDetectorService (Phase 12)    -- faiblesses par spécialité
    ConfidenceCalibrator (Phase 12)       -- confiance calibrée (Wilson)
    EvalRunRepository/TaskRepository (Ph5)-- historique, provenance
    AgentRepository (Phase 3)             -- genome_ref par run
    CostGainEstimator (Phase 18)          -- coût réellement mesuré

READ-ONLY structurel : aucune méthode appelée ici n'écrit — uniquement
`list_*`/`get`/`detect_*`/`estimate_*`. Rien n'est jamais dupliqué en
stockage ; une divergence entre deux appels reflète un changement réel de
l'état sous-jacent, jamais une donnée qui aurait pu se désynchroniser.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from apex.calibration.calibrator import ConfidenceCalibrator
from apex.costgain.estimator import CostGainEstimator
from apex.evalrunner.model import EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.selfmodel.model import ProvenanceEntry, SelfModelView, SkillProvenance, SpecialtyKnowledge
from apex.skills.model import SkillStatus
from apex.skills.service import SkillRegistryService
from apex.tasks.repository import TaskRepository
from apex.weakness.service import WeaknessDetectorService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_clone_genome_ref(session_factory: Any, clone_id: str) -> str | None:
    from apex.db.engine import session_scope
    from apex.db.repositories import AgentRepository

    with session_scope(session_factory) as session:
        row = AgentRepository(session).get(clone_id)
        return row.genome_ref if row is not None else None


def _collect_eval_runs(
    task_repo: TaskRepository, eval_repo: EvalRunRepository, specialty: str,
) -> list[EvalRun]:
    runs: list[EvalRun] = []
    for task in task_repo.list_by_specialty(specialty):
        runs.extend(eval_repo.list_by_task(task.id))
    return runs


def get_self_model_view(
    session_factory: Any,
    *,
    specialties: Sequence[str],
    weakness_service: WeaknessDetectorService | None = None,
    skill_service: SkillRegistryService | None = None,
    calibrator: ConfidenceCalibrator | None = None,
    cost_estimator: CostGainEstimator | None = None,
    eval_repo: EvalRunRepository | None = None,
    task_repo: TaskRepository | None = None,
) -> SelfModelView:
    """Une entrée par élément de `specialties` — l'appelant décide du
    périmètre (`apex.specialties.registry.all()` pour tout couvrir). Une
    spécialité sans aucun `EvalRun` reçoit honnêtement `INSUFFICIENT_DATA`
    et des listes vides, jamais une absence silencieuse ni une valeur
    devinée."""
    weakness_service = weakness_service or WeaknessDetectorService(session_factory)
    skill_service = skill_service or SkillRegistryService(session_factory)
    calibrator = calibrator or ConfidenceCalibrator()
    cost_estimator = cost_estimator or CostGainEstimator()
    eval_repo = eval_repo or EvalRunRepository(session_factory)
    task_repo = task_repo or TaskRepository(session_factory)

    knowledge: list[SpecialtyKnowledge] = []
    for specialty in specialties:
        all_skills = skill_service.list_by_specialty(specialty)
        acquired = tuple(s for s in all_skills if s.status is SkillStatus.ACQUIRED)
        proposed = tuple(s for s in all_skills if s.status is SkillStatus.PROPOSED)

        report = weakness_service.detect_for_specialty(specialty)
        calibrated = calibrator.calibrate_from_weakness_report(report)

        eval_runs = _collect_eval_runs(task_repo, eval_repo, specialty)
        methods_used = tuple(sorted({run.method for run in eval_runs}, key=lambda m: m.value))
        genome_refs = sorted(
            {_get_clone_genome_ref(session_factory, run.clone_id) for run in eval_runs} - {None}
        )
        cost = cost_estimator.estimate_cost(eval_runs)

        last_validated = max(
            (s.decided_at for s in acquired if s.decided_at is not None), default=None,
        )

        knowledge.append(
            SpecialtyKnowledge(
                specialty=specialty, acquired_skills=acquired, proposed_skills=proposed,
                weakness_report=report, calibrated_confidence=calibrated,
                eval_run_count=len(eval_runs), methods_used=methods_used,
                genome_refs_used=tuple(genome_refs), cost=cost,
                last_skill_validated_at=last_validated,
            )
        )

    return SelfModelView(specialties=tuple(knowledge), generated_at=_utcnow())


def trace_skill_provenance(
    session_factory: Any,
    skill_id: str,
    *,
    skill_service: SkillRegistryService | None = None,
    eval_repo: EvalRunRepository | None = None,
    task_repo: TaskRepository | None = None,
) -> SkillProvenance:
    """« Pourquoi APEX pense-t-il savoir cela ? » — reconstruit la chaîne
    Skill -> evidence_run_ids -> EvalRun -> Task/clone/genome_ref/dataset/
    oracle -> résultat, entièrement à la lecture, sans nouvelle table. Un
    `evidence_run_id` devenu introuvable (ne devrait pas arriver —
    `SkillRegistryService._validate_evidence` vérifie l'existence à la
    proposition) est simplement omis, jamais fabriqué."""
    skill_service = skill_service or SkillRegistryService(session_factory)
    eval_repo = eval_repo or EvalRunRepository(session_factory)
    task_repo = task_repo or TaskRepository(session_factory)

    skill = skill_service.get(skill_id)
    if skill is None:
        raise LookupError(f"compétence « {skill_id} » introuvable")

    entries: list[ProvenanceEntry] = []
    for run_id in skill.evidence_run_ids:
        run = eval_repo.get(run_id)
        if run is None:
            continue
        task = task_repo.get(run.task_id)
        entries.append(
            ProvenanceEntry(
                eval_run_id=run.id, task_id=run.task_id,
                task_specialty=task.specialty if task is not None else skill.specialty,
                clone_id=run.clone_id, clone_genome_ref=_get_clone_genome_ref(session_factory, run.clone_id),
                method=run.method, oracle_verdict=run.oracle_verdict, oracle_detail=run.oracle_detail,
                dataset_ref=run.dataset_ref, created_at=run.created_at,
            )
        )
    return SkillProvenance(skill=skill, entries=tuple(entries))
