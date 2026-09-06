"""`BugFarmService` — suivi RÉEL de bugs, dérivé EXCLUSIVEMENT de
`apex.metalearner.weakness_engine.WeaknessEngineService.aggregate` (N+9,
INCHANGÉ) — passe « Adversarial Curriculum + Capability Benchmarking »,
§7 de la mission.

**Règles de transition DÉTERMINISTES** (fonction pure de
`GauntletWeakness.improvement_status`, N+9, jamais un état devinée) :

    (absent)   -> OPEN       — première observation d'une faiblesse.
    OPEN       -> FIXED      — `ImprovementStatus.RESOLVED` (N+9 : absente
                               des deux dernières générations).
    FIXED      -> REGRESSED  — la MÊME faiblesse réapparaît
                               (`PERSISTENT`/`RECOVERING`) après avoir été
                               `FIXED` — « le bug corrigé est revenu »
                               (§7/§15 de la mission).
    REGRESSED  -> FIXED      — se résout de nouveau (symétrique à
                               OPEN -> FIXED, jamais bloqué dans un état
                               terminal).

Ne réimplémente aucune agrégation : `WeaknessEngineService.aggregate` reste
l'UNIQUE source de vérité sur « cette faiblesse est-elle actuellement
observée, récupérée, résolue »."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apex.metalearner.bug_farm_model import BugRecord, BugStatus
from apex.metalearner.challenge_model import weakness_key
from apex.metalearner.judge_feedback_model import GauntletWeakness, ImprovementStatus

if TYPE_CHECKING:
    from apex.metalearner.bug_farm_repository import BugRecordRepository


class BugFarmService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def sync_from_weakness_history(self, specialty: str, current_generation: int) -> tuple[BugRecord, ...]:
        """Appelé après chaque `evolve_generation` — synchronise
        `bug_records` avec l'état RÉEL déjà agrégé par `WeaknessEngineService.
        aggregate`. Idempotent : rappeler avec le même état ne produit
        aucune transition supplémentaire (une transition ne dépend que de
        `(status actuel, improvement_status observé)`)."""
        from apex.metalearner.bug_farm_repository import BugRecordRepository
        from apex.metalearner.weakness_engine import WeaknessEngineService

        weaknesses = WeaknessEngineService(self._session_factory).aggregate(specialty)
        repo = BugRecordRepository(self._session_factory)
        updated: list[BugRecord] = []
        for weakness in weaknesses:
            key = weakness_key(specialty, weakness.dimension, weakness.failure_category.value)
            existing = repo.get_by_weakness(key)
            if existing is None:
                created = repo.create(self._new_bug(specialty, key, weakness))
                updated.append(created)
                continue
            transitioned = self._apply_transition(repo, existing, weakness, current_generation)
            if transitioned is not None:
                updated.append(transitioned)
        return tuple(updated)

    def _new_bug(self, specialty: str, key: str, weakness: GauntletWeakness) -> BugRecord:
        representative_member = weakness.affected_candidates[0] if weakness.affected_candidates else ""
        representative_genome = weakness.affected_genomes[0] if weakness.affected_genomes else ""
        return BugRecord.new(
            specialty=specialty, weakness_id=key, dimension=weakness.dimension,
            failure_category=weakness.failure_category.value, severity=weakness.severity,
            first_seen_generation=weakness.first_seen_generation,
            first_seen_at=weakness.first_seen,
            member_id=representative_member, genome_version_id=representative_genome,
            evidence_reference=None,
        )

    def _apply_transition(
        self, repo: BugRecordRepository, existing: BugRecord, weakness: GauntletWeakness,
        current_generation: int,
    ) -> BugRecord | None:
        if existing.status is BugStatus.OPEN and weakness.improvement_status is ImprovementStatus.RESOLVED:
            return repo.update_status(
                existing.id, status=BugStatus.FIXED.value, fixed_at_generation=current_generation,
            )
        if existing.status is BugStatus.FIXED and weakness.improvement_status in (
            ImprovementStatus.PERSISTENT, ImprovementStatus.RECOVERING,
        ):
            return repo.update_status(
                existing.id, status=BugStatus.REGRESSED.value, regressed_at_generation=current_generation,
            )
        if (
            existing.status is BugStatus.REGRESSED
            and weakness.improvement_status is ImprovementStatus.RESOLVED
        ):
            return repo.update_status(
                existing.id, status=BugStatus.FIXED.value, fixed_at_generation=current_generation,
            )
        return None

    def list_bugs(self, specialty: str, *, limit: int = 200) -> tuple[BugRecord, ...]:
        from apex.metalearner.bug_farm_repository import BugRecordRepository

        return tuple(BugRecordRepository(self._session_factory).list_by_specialty(specialty, limit=limit))

    def list_regressed(self, specialty: str) -> tuple[BugRecord, ...]:
        """§7/§15 : bugs dont la correction ne s'est PAS avérée durable —
        exploitable directement comme signal « improvement was not
        durable » (Phase 15 de la mission)."""
        return tuple(bug for bug in self.list_bugs(specialty) if bug.status is BugStatus.REGRESSED)

    # -- BUG_FARM réel (passe « Universal Task + Curriculum Engine », N+11,
    # § Phase 12 de la mission) ------------------------------------------

    def build_bug_farm_challenges(
        self, specialty: str, generation: int, *, limits: Any = None, budget: Any = None,
    ) -> tuple[Any, ...]:
        """Sélectionne les bugs PERTINENTS (`OPEN`/`REGRESSED` — un bug
        `FIXED` n'a rien à reproduire), produit une VRAIE tâche de
        reproduction (`apex.taskgen.deterministic.DeterministicTaskGenerator.
        _bug_farm`, jamais un contenu inventé), l'exécute via le chemin
        EXISTANT (Gauntlet/EvalRunner, inchangé — ce module ne fait QUE
        générer la tâche et le `CurriculumChallenge`, jamais une seconde
        exécution), enregistre la preuve (`BugRecord.evidence_reference`,
        écrite UNE SEULE fois — jamais écrasée) et alimente le curriculum.

        N'invente JAMAIS une tâche « parce qu'un bug existe » : sans tâche
        de base RÉELLEMENT résolvable (`evidence_reference` déjà posé, ou
        la preuve la plus récente de la même faiblesse), AUCUN challenge
        n'est produit pour ce bug (§12 : « la tâche doit être réellement
        exécutable et liée à une preuve »)."""
        from apex.metalearner.challenge_model import (
            ChallengeSource,
            CurriculumChallenge,
            difficulty_from_severity,
        )
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository

        eligible_statuses = (BugStatus.OPEN, BugStatus.REGRESSED)
        eligible = [b for b in self.list_bugs(specialty) if b.status in eligible_statuses]
        eligible.sort(key=lambda b: (-difficulty_from_severity(b.severity), b.weakness_id))
        if budget is not None:
            eligible = eligible[: budget.max_bug_farm_per_generation]

        repo = CurriculumChallengeRepository(self._session_factory)
        created = []
        for bug in eligible:
            existing = repo.list_by_weakness(bug.weakness_id, limit=200)
            if any(c.generation == generation and c.source is ChallengeSource.BUG_FARM for c in existing):
                continue  # idempotent par génération — même discipline que CurriculumChallengeService.
            base_task = self._resolve_base_task(bug)
            if base_task is None:
                continue
            task = self._generate_and_persist_bug_farm_task(bug, base_task, limits)
            if task is None:
                continue
            challenge = CurriculumChallenge.new(
                specialty=specialty, weakness_id=bug.weakness_id, dimension=bug.dimension,
                difficulty=difficulty_from_severity(bug.severity), generation=generation, task_id=task.id,
                evaluation_criteria=(
                    f"reproduction du bug « {bug.id} » ({bug.failure_category}) — vérifie que la "
                    "correction reste durable (apex.metalearner.bug_farm_service)."
                ),
                source=ChallengeSource.BUG_FARM, heldout=False, adversarial=False,
                parent_task_id=base_task.id,
            )
            created.append(repo.create(challenge))
            if bug.evidence_reference is None:
                from apex.metalearner.bug_farm_repository import BugRecordRepository

                BugRecordRepository(self._session_factory).set_evidence_reference(
                    bug.id, evidence_reference=task.id,
                )
        return tuple(created)

    def _resolve_base_task(self, bug: BugRecord) -> Any:
        from apex.tasks.repository import TaskRepository

        if bug.evidence_reference is not None:
            task = TaskRepository(self._session_factory).get(bug.evidence_reference)
            if task is not None:
                return task
        # Repli : la preuve la plus récente pour la MÊME faiblesse — même
        # mécanisme que `apex.metalearner.curriculum_engine.
        # CurriculumEngineService._most_recent_task_id` (N+9), réutilisé
        # ici sans duplication, jamais une tâche fabriquée.
        from apex.evalrunner.repository import EvalRunRepository
        from apex.metalearner.weakness_engine import WeaknessEngineService

        weakness_service = WeaknessEngineService(self._session_factory)
        history = weakness_service.history(bug.specialty, dimension=bug.dimension)
        relevant = [
            s for s in history
            if s.failure_category is not None and s.failure_category.value == bug.failure_category
            and s.evidence_reference is not None
        ]
        if not relevant:
            return None
        most_recent = max(relevant, key=lambda s: s.created_at)
        evidence_reference = most_recent.evidence_reference
        if evidence_reference is None:
            return None
        eval_run = EvalRunRepository(self._session_factory).get(evidence_reference)
        if eval_run is None:
            return None
        return TaskRepository(self._session_factory).get(eval_run.task_id)

    def _generate_and_persist_bug_farm_task(self, bug: BugRecord, base_task: Any, limits: Any) -> Any:
        from apex.policy.model import Subject
        from apex.taskgen.model import TaskKind
        from apex.taskgen.task_factory import TaskSpec, UniversalTaskFactory
        from apex.tasks.service import TaskBankService

        spec = TaskSpec(
            kind=TaskKind.BUG_FARM, specialty=base_task.specialty, split=base_task.split,
            base_task=base_task, limits=limits, weakness_id=bug.weakness_id, bug_record_id=bug.id,
        )
        try:
            generated = UniversalTaskFactory().create(spec)
        except Exception:
            # § Phase 17 de la mission N+9 (reconduit) : jamais laisser une
            # panne de génération casser la synchronisation bug farm.
            return None
        return TaskBankService(self._session_factory).create(
            split=generated.split, specialty=generated.specialty, title=generated.title,
            description=generated.description, command=generated.command, created_by=Subject.HUMAN,
            dataset_ref=generated.dataset_ref,
        )
