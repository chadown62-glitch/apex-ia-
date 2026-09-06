"""`TaskProvenanceService` — reconstruit « pourquoi cette tâche existe »
(passe « Universal Task + Curriculum Engine », N+11, § Phase 16 de la
mission) à partir de la preuve DÉJÀ persistée — jamais une seconde table de
provenance : `apex.metalearner.challenge_repository.
CurriculumChallengeRepository` reste l'UNIQUE source de vérité (indexée par
`task_id`, § Phase 23 : « réutiliser les tables existantes »).

Une tâche sans `CurriculumChallenge` associé (écrite à la main, `Subject.
HUMAN`, hors curriculum) a une provenance `None` — jamais fabriquée."""

from __future__ import annotations

from typing import Any

from apex.taskgen.model import TaskProvenance


class TaskProvenanceService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def get_provenance(self, task_id: str) -> TaskProvenance | None:
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository

        challenge = CurriculumChallengeRepository(self._session_factory).get_by_task_id(task_id)
        if challenge is None:
            return None
        return TaskProvenance(
            weakness_id=challenge.weakness_id, parent_task_id=challenge.parent_task_id,
            challenge_id=challenge.id, bug_record_id=None, benchmark_id=None,
            generator_version=challenge.protocol_version, seed=challenge.seed,
        )

    def get_variants(self, task_id: str, *, limit: int = 200) -> tuple[Any, ...]:
        """§ Phase 6/20 — tous les `CurriculumChallenge` dont `task_id` est
        la tâche PARENTE (`parent_task_id`) — les variantes RÉELLEMENT
        produites par `apex.taskgen.variant_engine.VariantEngine` à partir
        de cette tâche, jamais une liste devinée."""
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository

        repo = CurriculumChallengeRepository(self._session_factory)
        return tuple(repo.list_by_parent_task_id(task_id, limit=limit))
