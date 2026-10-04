"""`TaskRun` — l'exécution PERSISTÉE d'une tâche par `apex.taskloop.loop.run_task_loop`.

Audit « première boucle d'exécution réelle » (03/10/2026) : la chaîne TASK -> MODÈLE -> EXÉCUTION ->
RÉSULTAT -> ÉVALUATION -> EXPÉRIENCE existait (`run_task_loop` : routage réel, génération par un
`ModelProvider`, sandbox réelle, Judge Tier, recovery, `Experience`), mais :
- aucun point d'entrée produit ne l'appelait (seuls des tests) ;
- son résultat d'ensemble (`TaskResult`) n'existait qu'en mémoire du processus.

Ce module ne réimplémente rien de la boucle : `TaskRunService.start` enregistre une ligne `running`
(`task_runs`, migration c8e2f4a6b9d1), exécute `run_task_loop` tel quel dans un fil d'arrière-plan avec
`trace_id` = id de la TaskRun (la clé déjà portée par ses `EvalRun.experiment_ref` et ses événements),
puis enregistre le `TaskResult` final. Au démarrage du service (un par processus `apex serve`), toute
ligne encore `running` appartient à un processus disparu : elle devient `interrupted` — jamais reprise,
jamais devinée (`run_task_loop` n'a pas de reprise).

Aucune décision de jugement ni de récupération ici : verdicts, tentatives et arrêt restent ceux de
`run_task_loop` (Judge Tier/`OracleProvider`, `apex.recovery`)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from apex.policy.model import Subject
from apex.taskloop.request import TaskExecutionMode, TaskLimits, TaskRequest
from apex.taskloop.result import TaskResult

#: Même ordre de grandeur que la troncature des sorties candidates (`apex.taskloop.loop`) : la sortie
#: complète reste dans l'`EvalRun` de la tentative réussie.
_MAX_STORED_OUTPUT_CHARS = 20_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    #: `run_task_loop` a levé (tâche introuvable, `TaskAlreadyRunning`, erreur inattendue) — jamais un verdict.
    ERROR = "error"
    #: Le processus qui l'exécutait a disparu (arrêt d'APEX en cours de route).
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self is not TaskRunStatus.RUNNING


@dataclass(frozen=True)
class TaskRun:
    id: str
    task_id: str
    status: TaskRunStatus
    objective: str
    execution_mode: str
    requested_by: str
    limits: dict[str, Any]
    started_at: datetime
    providers_tried: tuple[str, ...] = ()
    eval_run_ids: tuple[str, ...] = ()
    attempts: tuple[dict[str, Any], ...] = ()
    final_output: str | None = None
    failure: str | None = None
    stop_reason: str | None = None
    completed_at: datetime | None = None
    #: TaskRun dont les `Experience` conditionnent la stratégie ; None : bras « baseline ».
    condition_on_run: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status.value,
            "objective": self.objective,
            "execution_mode": self.execution_mode,
            "requested_by": self.requested_by,
            "limits": dict(self.limits),
            "providers_tried": list(self.providers_tried),
            "eval_run_ids": list(self.eval_run_ids),
            "attempts": [dict(a) for a in self.attempts],
            "final_output": self.final_output,
            "failure": self.failure,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "condition_on_run": self.condition_on_run,
        }


class TaskRunRepository:
    """Même patron que `EvalRunRepository`/`ExperienceRepository` : imports `apex.db` différés."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, run: TaskRun) -> None:
        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            session.add(
                TaskRunRow(
                    id=run.id,
                    task_id=run.task_id,
                    status=run.status.value,
                    objective=run.objective,
                    execution_mode=run.execution_mode,
                    requested_by=run.requested_by,
                    limits=dict(run.limits),
                    providers_tried=list(run.providers_tried),
                    eval_run_ids=list(run.eval_run_ids),
                    attempts=[dict(a) for a in run.attempts],
                    final_output=run.final_output,
                    failure=run.failure,
                    stop_reason=run.stop_reason,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    condition_on_run=run.condition_on_run,
                )
            )

    def _finish(self, run_id: str, *, status: TaskRunStatus, values: dict[str, Any]) -> bool:
        """UNE transition `running` -> terminal, jamais deux : une ligne déjà terminale n'est pas réécrite
        (un résultat tardif d'un fil ne remplace jamais `interrupted`, et inversement)."""
        from sqlalchemy import update

        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            result = session.execute(
                update(TaskRunRow)
                .where(TaskRunRow.id == run_id, TaskRunRow.status == TaskRunStatus.RUNNING.value)
                .values(status=status.value, completed_at=_utcnow(), **values)
            )
            return bool(result.rowcount)

    def finish_with_result(self, run_id: str, result: TaskResult) -> bool:
        output = result.final_output
        if output is not None and len(output) > _MAX_STORED_OUTPUT_CHARS:
            output = output[:_MAX_STORED_OUTPUT_CHARS]
        return self._finish(
            run_id,
            status=TaskRunStatus(result.status.value),
            values={
                "providers_tried": list(result.providers_tried),
                "eval_run_ids": list(result.eval_run_ids),
                "attempts": [a.to_dict() for a in result.attempts],
                "final_output": output,
                "failure": result.failure,
                "stop_reason": result.stop_reason,
            },
        )

    def finish_with_error(self, run_id: str, error: str) -> bool:
        return self._finish(
            run_id, status=TaskRunStatus.ERROR, values={"failure": error[:2000], "stop_reason": "error"}
        )

    def mark_interrupted_running(self) -> int:
        """Au démarrage : toute TaskRun encore `running` appartient à un processus disparu."""
        from sqlalchemy import update

        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            result = session.execute(
                update(TaskRunRow)
                .where(TaskRunRow.status == TaskRunStatus.RUNNING.value)
                .values(
                    status=TaskRunStatus.INTERRUPTED.value,
                    completed_at=_utcnow(),
                    stop_reason="process_restarted",
                    failure="le processus APEX qui exécutait cette tâche s'est arrêté avant la fin (aucune reprise)",
                )
            )
            return int(result.rowcount or 0)

    def get(self, run_id: str) -> TaskRun | None:
        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            row = session.get(TaskRunRow, run_id)
            return _from_row(row) if row is not None else None

    def list_by_task(self, task_id: str, limit: int = 50) -> list[TaskRun]:
        from sqlalchemy import select

        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(TaskRunRow)
                .where(TaskRunRow.task_id == task_id)
                .order_by(TaskRunRow.started_at.desc())
                .limit(limit)
            ).all()
            return [_from_row(r) for r in rows]


def _from_row(row: Any) -> TaskRun:
    return TaskRun(
        id=row.id,
        task_id=row.task_id,
        status=TaskRunStatus(row.status),
        objective=row.objective,
        execution_mode=row.execution_mode,
        requested_by=row.requested_by,
        limits=dict(row.limits or {}),
        started_at=row.started_at,
        providers_tried=tuple(row.providers_tried or ()),
        eval_run_ids=tuple(row.eval_run_ids or ()),
        attempts=tuple(row.attempts or ()),
        final_output=row.final_output,
        failure=row.failure,
        stop_reason=row.stop_reason,
        completed_at=row.completed_at,
        condition_on_run=row.condition_on_run,
    )


def execution_pool(config: Any) -> Any:
    """Le pool qui reçoit le clone d'exécution : le premier pool ACTIF déclarant la capacité `sandbox` —
    jamais un pool `target` (machine pas encore réelle, voir config.yaml)."""
    from apex.config.schema import Capability, PoolStatus

    for pool in config.pools.values():
        if pool.status is PoolStatus.ACTIVE and Capability.SANDBOX in pool.capabilities:
            return pool
    raise LookupError(
        "aucun pool de calcul actif ne déclare la capacité « sandbox » (config.yaml, compute.pools)"
    )


@dataclass
class TaskRunService:
    """Démarre une exécution réelle et la rend observable. Une instance par processus (`apex serve`)."""

    session_factory: Any
    agent_runtime: Any
    pool: Any
    run_loop: Any = None  # `run_task_loop` (injectable pour les tests du SERVICE, jamais de la boucle)
    _threads: dict[str, threading.Thread] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.run_loop is None:
            from apex.taskloop.loop import run_task_loop

            self.run_loop = run_task_loop
        self.repo = TaskRunRepository(self.session_factory)

    def reconcile_on_startup(self) -> int:
        return self.repo.mark_interrupted_running()

    def start(
        self,
        *,
        task_id: str,
        objective: str,
        execution_mode: TaskExecutionMode = TaskExecutionMode.BOUNDED,
        limits: TaskLimits | None = None,
        generation_timeout_s: float = 120.0,
        requested_by: Subject = Subject.HUMAN,
        wait: bool = False,
        condition_on_run: str | None = None,
    ) -> TaskRun:
        from apex.taskloop.registry import default_registry
        from apex.tasks.repository import TaskRepository

        task = TaskRepository(self.session_factory).get(task_id)
        if task is None:
            raise LookupError(f"tâche « {task_id} » introuvable")
        if task.solution_harness is None:
            raise ValueError(
                f"la tâche « {task_id} » ne porte pas de « solution_harness » : rien à exécuter"
            )
        if task_id in default_registry():
            raise RuntimeError(f"une exécution de la tâche « {task_id} » est déjà en cours")
        if condition_on_run is not None:
            prior = self.repo.get(condition_on_run)
            if prior is None:
                raise ValueError(f"TaskRun de référence « {condition_on_run} » introuvable")
            if prior.task_id != task_id:
                # Jamais l'expérience d'une AUTRE tâche : aucune contamination entre tâches.
                raise ValueError(
                    f"la TaskRun « {condition_on_run} » porte sur une autre tâche ({prior.task_id})"
                )
            if not prior.status.terminal:
                raise ValueError(f"la TaskRun « {condition_on_run} » n'est pas terminée ({prior.status.value})")
        limits = limits or TaskLimits(
            max_attempts=3, max_retries=2, max_candidates=2, max_wall_clock_s=900.0
        )
        request = TaskRequest(
            task_id=task_id,
            objective=objective,
            execution_mode=execution_mode,
            limits=limits,
            condition_on_run=condition_on_run,
        )  # `authorization` : défaut de `TaskRequest` (CORE), celui avec lequel la boucle est éprouvée
        run = TaskRun(
            id=str(uuid.uuid4()),
            task_id=task_id,
            status=TaskRunStatus.RUNNING,
            objective=objective,
            execution_mode=execution_mode.value,
            requested_by=requested_by.value,
            limits={
                "max_attempts": limits.max_attempts,
                "max_retries": limits.max_retries,
                "max_candidates": limits.max_candidates,
                "max_wall_clock_s": limits.max_wall_clock_s,
                "generation_timeout_s": generation_timeout_s,
            },
            started_at=_utcnow(),
            condition_on_run=condition_on_run,
        )
        self.repo.create(run)

        def execute() -> None:
            try:
                result = self.run_loop(
                    request,
                    session_factory=self.session_factory,
                    agent_runtime=self.agent_runtime,
                    pool=self.pool,
                    generation_timeout_s=generation_timeout_s,
                    trace_id=run.id,
                )
                self.repo.finish_with_result(run.id, result)
            except Exception as exc:  # erreur d'appel ou panne inattendue : enregistrée, jamais avalée
                self.repo.finish_with_error(run.id, f"{exc.__class__.__name__}: {exc}")
            finally:
                self._threads.pop(run.id, None)

        thread = threading.Thread(target=execute, name=f"apex-taskrun-{run.id[:8]}", daemon=True)
        self._threads[run.id] = thread
        thread.start()
        if wait:
            thread.join()
            return self.repo.get(run.id) or run
        return run

    def wait(self, run_id: str, timeout_s: float | None = None) -> TaskRun | None:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout_s)
        return self.repo.get(run_id)
