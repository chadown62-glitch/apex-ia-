"""EvalRunnerService — orchestrateur qui relie TaskBank, AgentRuntime,
Sandbox et OracleProvider — le premier véritable laboratoire expérimental
d'APEX (GO explicite Phase 5, voir `docs/PHASE_5_REPORT.md`) :

    TASK + CLONE -> AGENT RUNTIME -> SANDBOX -> ORACLE -> EVAL_RUN (persisté)

Même patron de composition qu'`apex.missions.execution.launch_mission` :
`AgentRuntime`/`SandboxProvider` sont injectés, jamais reconstruits ici —
ce module est le SEUL point qui compose TaskBank et EvalRunner, sans que
ces modules ne se connaissent mutuellement.

**Aucun raccordement à `ModelProvider`** : `task.command` est TOUJOURS une
commande déterministe déjà stockée sur la `Task` (voir `apex.tasks.model`),
jamais une tentative générée à la volée — décision de périmètre explicite
(GO explicite Phase 5 : seuls TaskBank/OracleProvider/EvalRunner et leurs
intégrations strictement nécessaires sont autorisés).

**Aucune promotion, aucune modification de Genome** : ce module ne fait
qu'exécuter une tâche et enregistrer un jugement — un laboratoire
d'évaluation, pas un système autonome d'amélioration.

Réutilise directement `Action.EXECUTE_IN_SANDBOX` (CORE seul) via
`AgentRuntime.execute_in_sandbox` — aucune nouvelle action `PolicyEngine`
n'est nécessaire ici : ce module ne fait qu'assembler des étapes déjà
individuellement autorisées (lecture de tâche, lecture de clone, exécution
sandbox), jamais un nouveau pouvoir.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.errors import IsolationUnavailable
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.oracles.model import OracleVerdict
from apex.oracles.provider import OracleProvider, get_default_oracle
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.sandbox.model import CancellationToken, ExecutionStatus, ResourceLimits, SandboxSpec
from apex.sandbox.provider import SandboxProvider, get_default_provider
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task
from apex.tasks.repository import TaskRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvalRunnerService:
    def __init__(
        self,
        session_factory: Any,
        *,
        agent_runtime: AgentRuntime,
        oracle: OracleProvider | None = None,
        sandbox: SandboxProvider | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._agent_runtime = agent_runtime
        self._oracle = oracle or get_default_oracle()
        self._sandbox = sandbox
        self._task_repo = TaskRepository(session_factory)
        self._repo = EvalRunRepository(session_factory)

    def run(
        self,
        *,
        task_id: str,
        clone_id: str,
        method: EvalMethod,
        seed: int | None = None,
        experiment_ref: str | None = None,
        limits: ResourceLimits | None = None,
        requested_by: Subject = Subject.CORE,
        cancel_token: CancellationToken | None = None,
        strategy_facts: dict[str, Any] | None = None,
    ) -> EvalRun:
        task = self._task_repo.get(task_id)
        if task is None:
            raise LookupError(f"tâche « {task_id} » introuvable")

        clone = self._get_clone(clone_id)
        if clone is None:
            raise ValueError(f"agent « {clone_id} » introuvable")

        # Identifiant du run décidé AVANT l'exécution : sert de corrélateur
        # (`SandboxSpec.mission_id`) entre les events sandbox et l'EvalRun
        # persisté — même rôle que `Mission.id` pour `launch_mission`, mais
        # aucune Mission n'est créée ici (laboratoire d'évaluation, pas
        # d'orchestration de mission).
        eval_run_id = str(uuid.uuid4())
        resolved_sandbox = self._sandbox or get_default_provider()
        resolved_limits = limits or self._default_limits()
        started_at = _utcnow()

        spec = SandboxSpec(
            mission_id=eval_run_id, clone_id=clone.clone_id, command=task.command, limits=resolved_limits,
        )

        try:
            sandbox_result = self._agent_runtime.execute_in_sandbox(
                clone, resolved_sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
            )
        except IsolationUnavailable as exc:
            # `AgentRuntime.execute_in_sandbox` a déjà journalisé (event +
            # audit) et fait échouer le clone — ce module ne fait
            # qu'enregistrer le résultat côté EvalRunner, jamais une
            # seconde fois le même événement.
            run = EvalRun(
                id=eval_run_id, task_id=task.id, clone_id=clone.clone_id, method=method,
                oracle_verdict=OracleVerdict.ERROR, oracle_detail=f"sandbox indisponible : {exc}",
                sandbox_status=ExecutionStatus.PERMISSION_DENIED, exit_code=None, elapsed_s=None,
                facts=({"strategy": strategy_facts} if strategy_facts else {}),
                created_by=requested_by.value, seed=seed, dataset_ref=task.dataset_ref,
                experiment_ref=experiment_ref, started_at=started_at, completed_at=_utcnow(),
            )
            self._repo.create(run)
            self._record_event(run, "evalrun.error", {"reason": str(exc)})
            return run

        completed_at = _utcnow()
        oracle_result = self._oracle.judge(sandbox_result=sandbox_result, task=task)
        run = self._build_eval_run(
            eval_run_id=eval_run_id, task=task, clone_id=clone.clone_id, method=method, seed=seed,
            experiment_ref=experiment_ref, requested_by=requested_by, sandbox_result=sandbox_result,
            oracle_verdict=oracle_result.verdict, oracle_detail=oracle_result.detail,
            started_at=started_at, completed_at=completed_at, strategy_facts=strategy_facts,
        )
        self._repo.create(run)
        self._record_event(
            run, "evalrun.completed",
            {"oracle_verdict": run.oracle_verdict.value, "sandbox_status": run.sandbox_status.value},
        )
        self._record_audit(
            run, actor=requested_by.value, action="evalrun.completed",
            details={"task_id": task.id, "clone_id": clone.clone_id, "oracle_verdict": run.oracle_verdict.value},
        )
        return run

    # -- lecture -------------------------------------------------------------

    def get(self, eval_run_id: str) -> EvalRun | None:
        return self._repo.get(eval_run_id)

    def list_all(self, limit: int = 200) -> list[EvalRun]:
        return self._repo.list_all(limit=limit)

    def list_by_task(self, task_id: str, limit: int = 200) -> list[EvalRun]:
        return self._repo.list_by_task(task_id, limit=limit)

    def list_by_clone(self, clone_id: str, limit: int = 200) -> list[EvalRun]:
        return self._repo.list_by_clone(clone_id, limit=limit)

    # -- internes -----------------------------------------------------------

    def _build_eval_run(
        self,
        *,
        eval_run_id: str,
        task: Task,
        clone_id: str,
        method: EvalMethod,
        seed: int | None,
        experiment_ref: str | None,
        requested_by: Subject,
        sandbox_result: SandboxResult,
        oracle_verdict: OracleVerdict,
        oracle_detail: str | None,
        started_at: datetime,
        completed_at: datetime,
        strategy_facts: dict[str, Any] | None = None,
    ) -> EvalRun:
        from apex.obs.status import DataStatus

        exit_code = (
            int(sandbox_result.exit_code.value)
            if sandbox_result.exit_code.status is DataStatus.REAL
            else None
        )
        elapsed_s = (
            float(sandbox_result.elapsed_s.value)
            if sandbox_result.elapsed_s.status is DataStatus.REAL
            else None
        )
        # `facts` reste le sac JSON libre déjà prévu par le contrat Phase 5
        # (aucune colonne ajoutée) : on y ajoute `memory_peak_mb` UNIQUEMENT
        # quand `SandboxResult` le mesure réellement (cgroups v1
        # `memory.max_usage_in_bytes`, réel sur cette machine) — jamais une
        # valeur inventée. `cpu_time_s` reste délibérément absent : `cpu.stat`
        # (v1) n'expose que du throttling, pas le temps CPU consommé (limite
        # réelle du backend, voir `apex.sandbox.linux_namespace`), donc
        # l'ajouter ne ferait que porter une clé toujours UNKNOWN.
        facts = dict(sandbox_result.facts)
        if sandbox_result.memory_peak_mb.status is DataStatus.REAL:
            facts["memory_peak_mb"] = float(sandbox_result.memory_peak_mb.value)
        # `strategy_facts` (ex. `MetaLearner`, `apex.metalearner.cycle`) vit
        # sous une clé imbriquée dédiée — jamais mélangé aux clés mesurées
        # par la sandbox, aucune collision possible entre les deux
        # namespaces. Toujours OPTIONNEL : `EvalRunnerService.run()` sans
        # `strategy_facts` se comporte exactement comme avant (contrat
        # Phase 5 inchangé pour tout appelant existant).
        if strategy_facts:
            facts["strategy"] = dict(strategy_facts)
        return EvalRun(
            id=eval_run_id, task_id=task.id, clone_id=clone_id, method=method,
            oracle_verdict=oracle_verdict, oracle_detail=oracle_detail,
            sandbox_status=sandbox_result.status, exit_code=exit_code, elapsed_s=elapsed_s,
            facts=facts, created_by=requested_by.value, seed=seed,
            dataset_ref=task.dataset_ref, experiment_ref=experiment_ref,
            started_at=started_at, completed_at=completed_at,
        )

    def _default_limits(self) -> ResourceLimits:
        from apex.config.loader import load_config

        config = load_config()
        return ResourceLimits.from_config(config.sandbox)

    def _get_clone(self, clone_id: str) -> Any:
        from apex.agents.model import Clone
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            row = AgentRepository(session).get(clone_id)
            return Clone.from_row(row) if row is not None else None

    def _record_event(self, run: EvalRun, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.evalrunner",
                msg=kind, context={"eval_run_id": run.id, "kind": kind, **details},
            )

    def _record_audit(self, run: EvalRun, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        # Écrit pour le compte de l'acteur réel — même patron que
        # MissionService._record_audit / AgentRuntime._record_audit.
        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=run.id, details=details,
            )
