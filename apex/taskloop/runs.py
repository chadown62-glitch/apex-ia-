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


#: `condition_on_run` symbolique : la dernière TaskRun TERMINÉE de la même tâche.
LATEST = "latest"


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
    #: Protocole v2 : stratégie tirée de `condition_on_run` (`rule` | `failure_facts`) et température de base.
    strategy: str | None = None
    base_temperature: float | None = None
    #: Ce que la boucle change entre deux tentatives (`TaskRequest.retry_adaptation`) et si l'étape PLAN
    #: a demandé une analyse au modèle (`TaskRequest.plan_first`). `None` : TaskRun antérieure à ces champs.
    retry_adaptation: str | None = None
    plan_first: bool | None = None
    #: Mode outillé : permissions explicitement accordées et tours maximaux par tentative (None : sans outils).
    tool_permissions: tuple[str, ...] | None = None
    max_tool_steps: int | None = None
    use_memory: bool | None = None
    #: Mission (gouvernance) qui a lancé cette TaskRun — `apex.missions.execution.run_mission_task`.
    mission_id: str | None = None
    context: str | None = None
    constraints: str | None = None
    #: Nombre de reprises après interruption (`TaskRunService.resume`) ; None : jamais reprise.
    resume_count: int | None = None

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
            "strategy": self.strategy,
            "base_temperature": self.base_temperature,
            "retry_adaptation": self.retry_adaptation,
            "plan_first": self.plan_first,
            "tool_permissions": list(self.tool_permissions) if self.tool_permissions is not None else None,
            "max_tool_steps": self.max_tool_steps,
            "use_memory": self.use_memory,
            "mission_id": self.mission_id,
            "resume_count": self.resume_count,
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
                    strategy=run.strategy,
                    base_temperature=run.base_temperature,
                    retry_adaptation=run.retry_adaptation,
                    plan_first=run.plan_first,
                    tool_permissions=list(run.tool_permissions) if run.tool_permissions is not None else None,
                    max_tool_steps=run.max_tool_steps,
                    use_memory=run.use_memory,
                    mission_id=run.mission_id,
                    context=run.context,
                    constraints=run.constraints,
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
                    failure=(
                        "le processus APEX qui exécutait cette tâche s'est arrêté avant la fin "
                        "(reprise possible : TaskRunService.resume, POST /task-runs/{id}/resume)"
                    ),
                )
            )
            return int(result.rowcount or 0)

    def claim_resume(self, run_id: str) -> bool:
        """`interrupted` -> `running` par UN UPDATE conditionnel : deux reprises concurrentes (ou répétées) ne
        peuvent jamais relancer deux boucles pour la même TaskRun — la seconde ne trouve plus `interrupted`."""
        from sqlalchemy import update

        from apex.db.engine import session_scope
        from apex.db.models import TaskRunRow

        with session_scope(self._session_factory) as session:
            row = session.get(TaskRunRow, run_id)
            count = (row.resume_count or 0) if row is not None else 0
            result = session.execute(
                update(TaskRunRow)
                .where(TaskRunRow.id == run_id, TaskRunRow.status == TaskRunStatus.INTERRUPTED.value)
                .values(status=TaskRunStatus.RUNNING.value, completed_at=None, failure=None, stop_reason=None,
                        resume_count=count + 1)
            )
            return bool(result.rowcount)

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
        strategy=row.strategy,
        base_temperature=row.base_temperature,
        retry_adaptation=row.retry_adaptation,
        plan_first=row.plan_first,
        tool_permissions=tuple(row.tool_permissions) if row.tool_permissions is not None else None,
        max_tool_steps=row.max_tool_steps,
        use_memory=row.use_memory,
        mission_id=getattr(row, "mission_id", None),
        context=getattr(row, "context", None),
        constraints=getattr(row, "constraints", None),
        resume_count=getattr(row, "resume_count", None),
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
    #: Registre des boucles en cours où chercher la poignée d'annulation (`cancel`) ; `None` : celui par défaut.
    registry: Any = None
    _threads: dict[str, threading.Thread] = field(default_factory=dict)
    #: Tâches RÉSERVÉES par une exécution acceptée (réservation atomique, avant le démarrage du fil) : deux
    #: lancements simultanés de la même tâche ne sont jamais tous deux acceptés (le second est refusé AVANT
    #: d'être créé, au lieu d'être accepté puis de finir en erreur `TaskAlreadyRunning` dans son fil).
    _reserved: set[str] = field(default_factory=set)
    _reserve_lock: threading.Lock = field(default_factory=threading.Lock)

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
        strategy: str = "rule",
        base_temperature: float | None = None,
        retry_adaptation: str = "failure_facts",
        plan_first: bool = False,
        tool_permissions: tuple[str, ...] = (),
        max_tool_steps: int = 8,
        use_memory: bool = False,
        context: str = "",
        constraints: str = "",
        mission_id: str | None = None,
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
        from apex.tasks.model import TaskKind

        if task.effective_kind is TaskKind.PROJECT and "workspace.write" not in tool_permissions:
            raise ValueError(
                f"la tâche « {task_id} » est un projet : elle exige le mode outillé (« workspace.write »)"
            )
        if task_id in (self.registry if self.registry is not None else default_registry()):
            raise RuntimeError(f"une exécution de la tâche « {task_id} » est déjà en cours")
        if condition_on_run == LATEST:
            # Apprendre de la DERNIÈRE exécution terminée de cette même tâche (résolue ici, puis enregistrée
            # par son identifiant réel : la provenance reste exacte).
            finished = [r for r in self.repo.list_by_task(task_id) if r.status.terminal]
            if not finished:
                raise ValueError(f"aucune exécution terminée de la tâche « {task_id} » dont apprendre")
            condition_on_run = finished[0].id
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
            strategy=strategy,
            base_temperature=base_temperature,
            retry_adaptation=retry_adaptation,
            plan_first=plan_first,
            tool_permissions=tuple(tool_permissions),
            max_tool_steps=max_tool_steps,
            use_memory=use_memory,
            context=context,
            constraints=constraints,
            mission_id=mission_id,
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
            strategy=strategy if condition_on_run else None,
            base_temperature=base_temperature,
            retry_adaptation=retry_adaptation,
            plan_first=plan_first,
            tool_permissions=tuple(tool_permissions) if tool_permissions else None,
            max_tool_steps=max_tool_steps if tool_permissions else None,
            use_memory=use_memory,
            mission_id=mission_id,
            context=context or None,
            constraints=constraints or None,
        )
        self._reserve(task_id)
        try:
            self.repo.create(run)
        except Exception:
            self._release(task_id)
            raise
        return self._launch(run, request, task, generation_timeout_s=generation_timeout_s, wait=wait)

    def _reserve(self, task_id: str) -> None:
        from apex.taskloop.registry import default_registry

        registry = self.registry if self.registry is not None else default_registry()
        with self._reserve_lock:
            if task_id in self._reserved or task_id in registry:
                raise RuntimeError(f"une exécution de la tâche « {task_id} » est déjà en cours")
            self._reserved.add(task_id)

    def _release(self, task_id: str) -> None:
        with self._reserve_lock:
            self._reserved.discard(task_id)

    def _launch(self, run: TaskRun, request: TaskRequest, task: Any, *, generation_timeout_s: float,
                wait: bool) -> TaskRun:
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
                self._remember(run.id, task)
            except Exception as exc:  # erreur d'appel ou panne inattendue : enregistrée, jamais avalée
                self.repo.finish_with_error(run.id, f"{exc.__class__.__name__}: {exc}")
            finally:
                self._threads.pop(run.id, None)
                self._release(run.task_id)

        thread = threading.Thread(target=execute, name=f"apex-taskrun-{run.id[:8]}", daemon=True)
        self._threads[run.id] = thread
        thread.start()
        if wait:
            thread.join()
            return self.repo.get(run.id) or run
        return run

    def _remember(self, run_id: str, task: Any) -> None:
        """Indexe l'exécution terminée dans la mémoire SYSTÈME (`apex.memory.indexing.index_task_run` : énoncé
        public, code produit, verdict, faits autorisés). Un échec d'indexation n'affecte jamais la TaskRun : il
        est journalisé (`memory.index_failed`), jamais avalé silencieusement."""
        from apex.evalrunner.repository import EvalRunRepository
        from apex.memory.indexing import index_task_run
        from apex.memory.service import MemoryService

        try:
            finished = self.repo.get(run_id)
            if finished is None:
                return
            eval_repo = EvalRunRepository(self.session_factory)
            eval_runs = [r for r in (eval_repo.get(i) for i in finished.eval_run_ids) if r is not None]
            index_task_run(MemoryService(self.session_factory), run=finished, task=task, eval_runs=eval_runs)
        except Exception as exc:
            from apex.db.engine import session_scope
            from apex.db.repositories import EventRepository

            with session_scope(self.session_factory) as session:
                EventRepository(session).add(
                    ts=_utcnow(), level="WARNING", logger="apex.taskloop", msg="memory.index_failed",
                    context={"trace_id": run_id, "kind": "memory.index_failed",
                             "error": f"{exc.__class__.__name__}: {exc}"[:500]},
                )

    def resume(self, run_id: str, *, wait: bool = False) -> TaskRun:
        """REPRISE RÉELLE d'une TaskRun `interrupted` (processus arrêté en cours de route) : même identifiant,
        même requête reconstruite depuis la ligne persistée, et la boucle CONTINUE depuis l'état persisté —
        tentatives déjà jugées (EvalRun/Experience) comptées dans le budget et non refaites, faits d'échec de la
        dernière transmis, espace de travail conservé, plan déjà rédigé réutilisé. Une tentative coupée avant
        son jugement n'a laissé aucune preuve : elle est refaite. Idempotente : la transition `interrupted` ->
        `running` est atomique (`claim_resume`), une seconde reprise est refusée.

        Refus explicites : TaskRun introuvable / pas `interrupted` ; TaskRun d'une MISSION (la mission orpheline a
        été close FAILED au redémarrage par `apex.scheduler.recovery` : sa reprise est une décision humaine —
        recréer la mission) ; une autre exécution de la même tâche en cours."""
        from apex.taskloop.registry import default_registry
        from apex.tasks.repository import TaskRepository

        run = self.repo.get(run_id)
        if run is None:
            raise LookupError(f"exécution « {run_id} » introuvable")
        if run.status is not TaskRunStatus.INTERRUPTED:
            raise ValueError(f"seule une exécution interrompue peut reprendre (état : {run.status.value})")
        if run.mission_id is not None:
            raise ValueError(
                f"l'exécution « {run_id} » appartient à la mission « {run.mission_id} », close au redémarrage : "
                "recréer la mission (décision humaine)"
            )
        task = TaskRepository(self.session_factory).get(run.task_id)
        if task is None:
            raise LookupError(f"tâche « {run.task_id} » introuvable")
        registry = self.registry if self.registry is not None else default_registry()
        if run.task_id in registry:
            raise RuntimeError(f"une exécution de la tâche « {run.task_id} » est déjà en cours")
        limits = dict(run.limits or {})
        request = TaskRequest(
            task_id=run.task_id, objective=run.objective, context=run.context or "",
            constraints=run.constraints or "", execution_mode=TaskExecutionMode(run.execution_mode),
            limits=TaskLimits(
                max_attempts=int(limits.get("max_attempts", 1)), max_retries=int(limits.get("max_retries", 0)),
                max_candidates=int(limits.get("max_candidates", 1)),
                max_wall_clock_s=float(limits.get("max_wall_clock_s", 300.0)),
            ),
            condition_on_run=run.condition_on_run, strategy=run.strategy or "rule",
            base_temperature=run.base_temperature, retry_adaptation=run.retry_adaptation or "failure_facts",
            plan_first=bool(run.plan_first), tool_permissions=tuple(run.tool_permissions or ()),
            max_tool_steps=run.max_tool_steps or 8, use_memory=bool(run.use_memory),
            resume=True, resume_plan=self._prior_plan(run) if run.plan_first else None,
        )
        self._reserve(run.task_id)
        if not self.repo.claim_resume(run_id):
            self._release(run.task_id)
            raise RuntimeError(f"l'exécution « {run_id} » a déjà été reprise")
        resumed = self.repo.get(run_id) or run
        return self._launch(resumed, request, task,
                            generation_timeout_s=float(limits.get("generation_timeout_s", 120.0)), wait=wait)

    def _prior_plan(self, run: TaskRun) -> str | None:
        """Dernier plan rédigé par le modèle pour cette TaskRun avant l'interruption (événement persisté)."""
        from datetime import timedelta

        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self.session_factory) as session:
            rows = EventRepository(session).in_window(
                logger="apex.taskloop", since=run.started_at - timedelta(seconds=5))
            plans = [r.context.get("plan") for r in rows
                     if (r.context or {}).get("trace_id") == run.id
                     and r.context.get("kind") in ("task.plan.generated", "task.plan.reused")]
        return plans[-1] if plans else None

    def cancel(self, run_id: str) -> bool:
        """Demande l'arrêt RÉEL d'une TaskRun en cours dans CE processus : le jeton d'annulation de sa boucle
        est déclenché (le processus sandbox en cours est tué, `check_stop` arrête la boucle avant la tentative
        suivante, l'épisode outillé s'arrête au tour suivant) ; la TaskRun se termine alors `cancelled`.
        `False` : aucune boucle de cette TaskRun ne tourne ici (déjà terminée, ou autre processus)."""
        from apex.taskloop.registry import default_registry

        registry = self.registry if self.registry is not None else default_registry()
        for handle in registry.snapshot():
            if handle.trace_id == run_id:
                handle.cancel_token.request_cancel()
                return True
        return False

    def wait(self, run_id: str, timeout_s: float | None = None) -> TaskRun | None:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout_s)
        return self.repo.get(run_id)
