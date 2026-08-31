"""Persistance des résultats d'évaluation — couche domaine au-dessus
d'`apex.db`. Traduit entre `EvalRun` (domaine, `apex.evalrunner.model`) et
`EvalRunRow` (`apex.db.models`), pour qu'`apex.evalrunner.service` ne
manipule jamais directement SQLAlchemy — même séparation qu'`apex.tasks.repository`
vis-à-vis d'`apex.db.repositories.TaskRepository`. Tous les imports vers
`apex.db` sont différés dans le corps des méthodes : `apex.evalrunner` doit
rester importable sans l'extra `db` installé.

**INSERT seul** — aucune méthode `update`/`delete` ici : un résultat
d'évaluation, positif ou négatif, n'est jamais modifié ni supprimé après coup.
"""

from __future__ import annotations

from typing import Any

from apex.evalrunner.model import EvalRun


class EvalRunRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, run: EvalRun) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepository

        with session_scope(self._session_factory) as session:
            _DbEvalRunRepository(session).create(
                id=run.id, task_id=run.task_id, clone_id=run.clone_id, method=run.method.value,
                seed=run.seed, dataset_ref=run.dataset_ref, experiment_ref=run.experiment_ref,
                oracle_verdict=run.oracle_verdict.value, oracle_detail=run.oracle_detail,
                sandbox_status=run.sandbox_status.value, exit_code=run.exit_code,
                elapsed_s=run.elapsed_s, facts=run.facts, created_by=run.created_by,
                started_at=run.started_at, completed_at=run.completed_at, created_at=run.created_at,
            )

    def get(self, eval_run_id: str) -> EvalRun | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepository

        with session_scope(self._session_factory) as session:
            row = _DbEvalRunRepository(session).get(eval_run_id)
            return EvalRun.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[EvalRun]:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepository

        with session_scope(self._session_factory) as session:
            rows = _DbEvalRunRepository(session).list_all(limit=limit)
            return [EvalRun.from_row(row) for row in rows]

    def list_by_task(self, task_id: str, limit: int = 200) -> list[EvalRun]:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepository

        with session_scope(self._session_factory) as session:
            rows = _DbEvalRunRepository(session).list_by_task(task_id, limit=limit)
            return [EvalRun.from_row(row) for row in rows]

    def list_by_clone(self, clone_id: str, limit: int = 200) -> list[EvalRun]:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepository

        with session_scope(self._session_factory) as session:
            rows = _DbEvalRunRepository(session).list_by_clone(clone_id, limit=limit)
            return [EvalRun.from_row(row) for row in rows]
