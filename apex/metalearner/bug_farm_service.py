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
