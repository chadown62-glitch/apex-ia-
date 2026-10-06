"""`Experience` — l'INTERPRÉTATION persistée d'un `EvalRun` produit par
`EvalRunnerService.run_generated_solution` (N+19), pour la boucle
d'apprentissage adaptative (N+20) : fitness RÉEL (`apex.metalearner.
population_fitness.compute_fitness`, réutilisé, jamais recalculé), catégorie
d'échec RÉELLE (`apex.experience.failure_taxonomy.classify_failure`,
réutilisée), et généalogie de génération (`generation_number`/
`parent_experience_id`/`mutation_description`) — voir la docstring
d'`ExperienceRow` (`apex.db.models`) pour la distinction complète avec
`EvalRun`.

Même séparation domaine/DB que `apex.evalrunner.model`/`apex.evalrunner.repository`
vis-à-vis d'`apex.db.models`/`apex.db.repositories` : ce module ne manipule
jamais SQLAlchemy directement, tous les imports vers `apex.db` sont différés
dans le corps des méthodes."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from apex.evalrunner.model import EvalRun
from apex.experience.failure_taxonomy import FailureCategory, classify_failure
from apex.experience.fitness import FitnessResult
from apex.oracles.model import OracleVerdict

if TYPE_CHECKING:
    from apex.db.models import ExperienceRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SuccessEvidence(StrEnum):
    """Force de la preuve portée par une expérience réussie. Une seule
    exécution qui passe est OBSERVED, jamais une preuve de référence : seul
    CONFIRMED (même verdict sur ré-exécution du même code, `RobustnessJudge`)
    autorise la boucle à exploiter la stratégie. REFUTED : une ré-exécution
    du même code a donné un autre verdict. NOT_APPLICABLE : pas un succès."""

    NOT_APPLICABLE = "not_applicable"
    OBSERVED = "observed"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


def _default_evidence(category: FailureCategory) -> SuccessEvidence:
    if category is FailureCategory.SUCCESS:
        return SuccessEvidence.OBSERVED
    return SuccessEvidence.NOT_APPLICABLE


def _fingerprint(eval_run: EvalRun) -> str | None:
    """SHA-256 du code candidat RÉELLEMENT généré (`facts["model_response"]
    ["text"]`, voir `EvalRunnerService._model_response_facts`) — `None` si
    aucune génération n'a abouti (`model_response` absent, ex. `generation_failure`).
    Un hash, jamais le texte lui-même une seconde fois : `Experience` ne
    duplique pas le stockage déjà fait par `EvalRun.facts`."""
    model_response = eval_run.facts.get("model_response")
    if not isinstance(model_response, dict):
        return None
    text = model_response.get("text")
    if not isinstance(text, str):
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Experience:
    id: str
    task_id: str
    eval_run_id: str
    candidate_id: str
    strategy_id: str
    generation_number: int
    generation_config: dict[str, Any]
    oracle_verdict: OracleVerdict
    fitness: float
    failure_category: FailureCategory
    created_by: str
    model_id: str | None = None
    solution_fingerprint: str | None = None
    execution_status: str | None = None
    exit_code: int | None = None
    parent_experience_id: str | None = None
    parent_generation: int | None = None
    mutation_description: str | None = None
    improvement_delta: float | None = None
    experiment_ref: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    success_evidence: SuccessEvidence | None = None

    def __post_init__(self) -> None:
        if self.success_evidence is None:
            object.__setattr__(self, "success_evidence", _default_evidence(self.failure_category))
        is_success = self.failure_category is FailureCategory.SUCCESS
        if is_success == (self.success_evidence is SuccessEvidence.NOT_APPLICABLE):
            raise ValueError(
                f"success_evidence={self.success_evidence} incompatible avec "
                f"failure_category={self.failure_category.value}"
            )

    @property
    def confirmed_success(self) -> bool:
        return self.success_evidence is SuccessEvidence.CONFIRMED

    @classmethod
    def from_row(cls, row: "ExperienceRow") -> Experience:
        return cls(
            id=row.id, task_id=row.task_id, eval_run_id=row.eval_run_id,
            candidate_id=row.candidate_id, model_id=row.model_id, strategy_id=row.strategy_id,
            generation_number=row.generation_number, generation_config=dict(row.generation_config or {}),
            solution_fingerprint=row.solution_fingerprint, execution_status=row.execution_status,
            exit_code=row.exit_code, oracle_verdict=OracleVerdict(row.oracle_verdict),
            fitness=row.fitness, failure_category=FailureCategory(row.failure_category),
            parent_experience_id=row.parent_experience_id, parent_generation=row.parent_generation,
            mutation_description=row.mutation_description, improvement_delta=row.improvement_delta,
            experiment_ref=row.experiment_ref, created_by=row.created_by, created_at=row.created_at,
            # NULL (ligne antérieure à la colonne) : jamais promue, relue comme simplement observée.
            success_evidence=SuccessEvidence(row.success_evidence) if row.success_evidence else None,
        )


def build_experience(
    eval_run: EvalRun,
    fitness: FitnessResult,
    *,
    strategy_id: str,
    generation_number: int,
    generation_config: dict[str, Any],
    created_by: str,
    parent_experience_id: str | None = None,
    parent_generation: int | None = None,
    mutation_description: str | None = None,
    improvement_delta: float | None = None,
    success_evidence: SuccessEvidence | None = None,
) -> Experience:
    """Construit une `Experience` à partir d'un `EvalRun` et d'un
    `FitnessResult` DÉJÀ réels (jamais recalculés ici — cette fonction est
    une PURE interprétation, aucun nouvel appel réseau/sandbox). `candidate_id`
    est l'`id` de l'`EvalRun` lui-même (une tentative = un candidat = un
    EvalRun, voir `EvalRunnerService.run_generated_solution`), jamais un
    identifiant distinct inventé."""
    return Experience(
        id=str(uuid.uuid4()), task_id=eval_run.task_id, eval_run_id=eval_run.id,
        candidate_id=eval_run.id, model_id=eval_run.model_id, strategy_id=strategy_id,
        generation_number=generation_number, generation_config=dict(generation_config),
        solution_fingerprint=_fingerprint(eval_run),
        execution_status=eval_run.sandbox_status.value if eval_run.sandbox_status else None,
        exit_code=eval_run.exit_code, oracle_verdict=eval_run.oracle_verdict, fitness=fitness.value,
        failure_category=classify_failure(eval_run), parent_experience_id=parent_experience_id,
        parent_generation=parent_generation, mutation_description=mutation_description,
        improvement_delta=improvement_delta, experiment_ref=eval_run.experiment_ref,
        created_by=created_by, created_at=_utcnow(), success_evidence=success_evidence,
    )


class ExperienceRepository:
    """Même patron que `apex.evalrunner.repository.EvalRunRepository` :
    traduit `Experience` <-> `ExperienceRow`, tous les imports `apex.db`
    différés dans le corps des méthodes."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, experience: Experience) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            _DbExperienceRepository(session).create(
                id=experience.id, task_id=experience.task_id, eval_run_id=experience.eval_run_id,
                candidate_id=experience.candidate_id, model_id=experience.model_id,
                strategy_id=experience.strategy_id, generation_number=experience.generation_number,
                generation_config=experience.generation_config,
                solution_fingerprint=experience.solution_fingerprint,
                execution_status=experience.execution_status, exit_code=experience.exit_code,
                oracle_verdict=experience.oracle_verdict.value, fitness=experience.fitness,
                failure_category=experience.failure_category.value,
                parent_experience_id=experience.parent_experience_id,
                parent_generation=experience.parent_generation,
                mutation_description=experience.mutation_description,
                improvement_delta=experience.improvement_delta, experiment_ref=experience.experiment_ref,
                success_evidence=(experience.success_evidence or SuccessEvidence.NOT_APPLICABLE).value,
                created_by=experience.created_by, created_at=experience.created_at,
            )

    def get(self, experience_id: str) -> Experience | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            row = _DbExperienceRepository(session).get(experience_id)
            return Experience.from_row(row) if row is not None else None

    def list_by_task(self, task_id: str, limit: int = 200) -> list[Experience]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExperienceRepository(session).list_by_task(task_id, limit=limit)
            return [Experience.from_row(row) for row in rows]

    def list_unattributed_generation_failures(self, limit: int = 500) -> list[Experience]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExperienceRepository(session).list_unattributed_generation_failures(limit=limit)
            return [Experience.from_row(row) for row in rows]

    def list_by_model_ids(self, model_ids: list[str], limit: int = 500) -> list[Experience]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExperienceRepository(session).list_by_model_ids(model_ids, limit=limit)
            return [Experience.from_row(row) for row in rows]

    def list_by_task_and_generation(
        self, task_id: str, generation_number: int, *, experiment_ref: str | None = None, limit: int = 200
    ) -> list[Experience]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ExperienceRepository as _DbExperienceRepository

        with session_scope(self._session_factory) as session:
            rows = _DbExperienceRepository(session).list_by_task_and_generation(
                task_id, generation_number, experiment_ref=experiment_ref, limit=limit,
            )
            return [Experience.from_row(row) for row in rows]
