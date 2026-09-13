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
from apex.config.schema import ModelEndpoint
from apex.errors import IsolationUnavailable, ModelProviderError, TimeoutExceeded
from apex.evalrunner.generated_solution import MissingCandidateMarkerError, build_candidate_command
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.genome import clone_config
from apex.models.model import GenerationRequest
from apex.models.provider import ModelProvider
from apex.models.provider import get_default_provider as get_default_model_provider
from apex.obs.status import DataStatus
from apex.oracles.model import OracleVerdict
from apex.oracles.provider import OracleProvider, get_default_oracle
from apex.oracles.response_judge import ResponseJudge, get_default_response_judge
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.sandbox.model import CancellationToken, ExecutionStatus, ResourceLimits, SandboxSpec
from apex.sandbox.provider import SandboxProvider, get_default_provider
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task
from apex.tasks.repository import TaskRepository

#: Sécurité (§10, passe « Real Model Evaluation ») — une réponse modèle
#: reste une DONNÉE persistée, jamais exécutée ; une limite de taille
#: raisonnable évite qu'une réponse pathologiquement longue ne gonfle
#: indéfiniment `facts` (colonne JSON). Jamais silencieuse : `truncated`
#: est toujours présent dans `facts["model_response"]` pour dire si cette
#: limite a été appliquée.
_MAX_STORED_RESPONSE_CHARS = 50_000


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
        genome_content: dict[str, Any] | None = None,
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
        # `genome_content` (passe « Real Clone Population + Evolution Loop »)
        # est la SEULE influence réelle, à ce jour, du contenu d'un Genome
        # sur une exécution — voir `apex.genome.clone_config`. Optionnel,
        # défaut `None` : tout appelant existant (`limits` explicite ou
        # aucun des deux) se comporte exactement comme avant.
        if limits is not None:
            resolved_limits = limits
        else:
            resolved_limits = clone_config.resolve_resource_limits(genome_content, self._default_limits())
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
            {
                "oracle_verdict": run.oracle_verdict.value,
                "sandbox_status": run.sandbox_status.value if run.sandbox_status else None,
            },
        )
        self._record_audit(
            run, actor=requested_by.value, action="evalrun.completed",
            details={
                "task_id": task.id,
                "clone_id": clone.clone_id,
                "oracle_verdict": run.oracle_verdict.value,
            },
        )
        return run

    def run_model_eval(
        self,
        *,
        task_id: str,
        endpoint: ModelEndpoint,
        method: EvalMethod,
        provider: ModelProvider | None = None,
        judge: ResponseJudge | None = None,
        clone_id: str | None = None,
        seed: int | None = None,
        experiment_ref: str | None = None,
        requested_by: Subject = Subject.CORE,
        timeout_s: float = 30.0,
    ) -> EvalRun:
        """Deuxième chemin d'exécution d'`EvalRunner` (passe « Real Model
        Evaluation »), DISTINCT de `run()` (chemin déterministe, inchangé) —
        les deux coexistent, aucun EvalRun existant n'est affecté :

            TASK -> MODEL PROVIDER -> MODEL RESPONSE -> EVAL_RUN (persisté)

        Jamais de sandbox ici (`clone_id`/`sandbox_status` restent `None` sur
        l'`EvalRun` produit, voir `EvalRunRow`) : le modèle est appelé au
        niveau ORCHESTRATEUR, exactement comme
        `apex.missions.execution.generate_mission_response` — jamais depuis
        un clone sandboxé, donc aucun contournement de la sandbox. `clone_id`
        reste un paramètre optionnel d'ATTRIBUTION (à qui/quoi rattacher cette
        tentative), jamais une exécution.

        Sécurité (§10) : `response.text` reste une DONNÉE — comparée par
        `judge` (égalité de chaîne), jamais exécutée, jamais interpolée dans
        une commande. Aucun secret n'est journalisé (`ModelProviderError` ne
        porte jamais le prompt/la réponse, voir `apex.errors`).
        """
        task = self._task_repo.get(task_id)
        if task is None:
            raise LookupError(f"tâche « {task_id} » introuvable")

        eval_run_id = str(uuid.uuid4())
        resolved_provider = provider or get_default_model_provider(endpoint)
        resolved_judge = judge or get_default_response_judge()
        started_at = _utcnow()

        request = GenerationRequest(endpoint=endpoint, prompt=task.description, timeout_s=timeout_s)
        try:
            generation = resolved_provider.generate(request)
        except (ModelProviderError, TimeoutExceeded) as exc:
            # Même discipline que `run()` sur `IsolationUnavailable` : un
            # échec de génération est enregistré, jamais relevé comme une
            # exception du point de vue de l'appelant — un `ModelProvider`
            # indisponible/en erreur est un résultat d'évaluation (ERROR),
            # pas une panne d'`EvalRunner` lui-même. `model_id` reste `None`
            # — aucun candidat n'a réellement été servi (même discipline que
            # `AgentExecutionRow.model_id`).
            run = EvalRun(
                id=eval_run_id, task_id=task.id, method=method,
                oracle_verdict=OracleVerdict.ERROR, oracle_detail=f"génération impossible : {exc}",
                exit_code=None, elapsed_s=None, facts={}, created_by=requested_by.value,
                clone_id=clone_id, sandbox_status=None, model_id=None,
                seed=seed, dataset_ref=task.dataset_ref, experiment_ref=experiment_ref,
                started_at=started_at, completed_at=_utcnow(),
            )
            self._repo.create(run)
            self._record_event(run, "evalrun.model_error", {"reason": str(exc)})
            return run

        completed_at = _utcnow()
        oracle_result = resolved_judge.judge(response=generation, task=task)
        run = EvalRun(
            id=eval_run_id, task_id=task.id, method=method,
            oracle_verdict=oracle_result.verdict, oracle_detail=oracle_result.detail,
            exit_code=None, elapsed_s=generation.elapsed_s,
            facts=self._model_response_facts(generation),
            created_by=requested_by.value, clone_id=clone_id, sandbox_status=None,
            model_id=generation.model_used, seed=seed, dataset_ref=task.dataset_ref,
            experiment_ref=experiment_ref, started_at=started_at, completed_at=completed_at,
        )
        self._repo.create(run)
        self._record_event(
            run, "evalrun.model_completed",
            {"oracle_verdict": run.oracle_verdict.value, "model_id": run.model_id},
        )
        self._record_audit(
            run, actor=requested_by.value, action="evalrun.model_completed",
            details={
                "task_id": task.id,
                "model_id": run.model_id,
                "oracle_verdict": run.oracle_verdict.value,
            },
        )
        return run

    def run_generated_solution(
        self,
        *,
        task_id: str,
        clone_id: str,
        endpoint: ModelEndpoint,
        method: EvalMethod,
        provider: ModelProvider | None = None,
        seed: int | None = None,
        experiment_ref: str | None = None,
        limits: ResourceLimits | None = None,
        genome_content: dict[str, Any] | None = None,
        requested_by: Subject = Subject.CORE,
        cancel_token: CancellationToken | None = None,
        generation_timeout_s: float = 30.0,
    ) -> EvalRun:
        """TROISIÈME chemin d'exécution d'`EvalRunner` (passe « Generated-
        Solution Evaluation Loop », N+19), DISTINCT de `run()` (script
        déterministe fixe) et de `run_model_eval()` (jugé par comparaison de
        texte, jamais sandboxé) :

            TASK (avec solution_harness) -> MODEL PROVIDER -> CODE CANDIDAT
                -> SANDBOX RÉEL -> ORACLE (code de sortie) -> EVAL_RUN
                (model_id ET sandbox_status persistés ensemble)

        C'est le premier chemin où une solution RÉELLEMENT générée par un
        `ModelProvider` (jamais un script fixe) est RÉELLEMENT exécutée dans
        le sandbox namespaced déjà réel et jugée par le même `OracleProvider`
        (code de sortie, preuve noyau) que le chemin déterministe — jamais un
        second vocabulaire de jugement. `clone_id` est ici une RÉELLE
        attribution d'exécution (contrairement à `run_model_eval`, cette
        tentative tourne bien dans un sandbox rattaché à ce clone).

        `genome_content` (optionnel, `None` par défaut) influence à la fois
        les limites d'exécution (`apex.genome.clone_config.
        resolve_resource_limits`, inchangé) et — nouveau dans cette passe —
        `GenerationRequest.temperature` via `apex.genome.clone_config.
        resolve_generation_temperature` : le SEUL paramètre de stratégie de
        génération câblé à ce jour (jamais un choix de modèle, voir la
        docstring de `resolve_generation_temperature`)."""
        task = self._task_repo.get(task_id)
        if task is None:
            raise LookupError(f"tâche « {task_id} » introuvable")
        if task.solution_harness is None:
            raise ValueError(
                f"la tâche « {task_id} » ne porte pas de « solution_harness » — "
                "run_generated_solution() exige une tâche de ce genre"
            )

        clone = self._get_clone(clone_id)
        if clone is None:
            raise ValueError(f"agent « {clone_id} » introuvable")

        eval_run_id = str(uuid.uuid4())
        resolved_provider = provider or get_default_model_provider(endpoint)
        temperature = clone_config.resolve_generation_temperature(genome_content)
        started_at = _utcnow()

        request = GenerationRequest(
            endpoint=endpoint, prompt=task.description, temperature=temperature,
            timeout_s=generation_timeout_s,
        )
        try:
            generation = resolved_provider.generate(request)
        except (ModelProviderError, TimeoutExceeded) as exc:
            # Même discipline que `run_model_eval` sur le même type
            # d'échec : une génération impossible est un résultat
            # d'évaluation (ERROR), jamais une exception remontée à
            # l'appelant. `sandbox_status`/`model_id` restent `None` —
            # aucune exécution sandboxée n'a même été tentée.
            run = EvalRun(
                id=eval_run_id, task_id=task.id, clone_id=clone.clone_id, method=method,
                oracle_verdict=OracleVerdict.ERROR, oracle_detail=f"génération impossible : {exc}",
                sandbox_status=None, exit_code=None, elapsed_s=None, model_id=None,
                facts={}, created_by=requested_by.value, seed=seed, dataset_ref=task.dataset_ref,
                experiment_ref=experiment_ref, started_at=started_at, completed_at=_utcnow(),
            )
            self._repo.create(run)
            self._record_event(run, "evalrun.generated_solution_generation_error", {"reason": str(exc)})
            return run

        candidate_command = build_candidate_command(
            solution_harness=task.solution_harness, candidate_code=generation.text,
        )
        resolved_sandbox = self._sandbox or get_default_provider()
        if limits is not None:
            resolved_limits = limits
        else:
            resolved_limits = clone_config.resolve_resource_limits(genome_content, self._default_limits())
        spec = SandboxSpec(
            mission_id=eval_run_id, clone_id=clone.clone_id, command=candidate_command,
            limits=resolved_limits,
        )

        try:
            sandbox_result = self._agent_runtime.execute_in_sandbox(
                clone, resolved_sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
            )
        except IsolationUnavailable as exc:
            # Même discipline que `run()` : `AgentRuntime.execute_in_sandbox`
            # a déjà journalisé (event + audit) et fait échouer le clone —
            # `model_id` reste renseigné (la génération, elle, a réellement
            # abouti) : seule l'exécution sandboxée a échoué.
            run = EvalRun(
                id=eval_run_id, task_id=task.id, clone_id=clone.clone_id, method=method,
                oracle_verdict=OracleVerdict.ERROR, oracle_detail=f"sandbox indisponible : {exc}",
                sandbox_status=ExecutionStatus.PERMISSION_DENIED, exit_code=None, elapsed_s=None,
                model_id=generation.model_used, facts=self._model_response_facts(generation),
                created_by=requested_by.value, seed=seed, dataset_ref=task.dataset_ref,
                experiment_ref=experiment_ref, started_at=started_at, completed_at=_utcnow(),
            )
            self._repo.create(run)
            self._record_event(run, "evalrun.generated_solution_sandbox_error", {"reason": str(exc)})
            return run

        completed_at = _utcnow()
        oracle_result = self._oracle.judge(sandbox_result=sandbox_result, task=task)
        run = self._build_eval_run(
            eval_run_id=eval_run_id, task=task, clone_id=clone.clone_id, method=method, seed=seed,
            experiment_ref=experiment_ref, requested_by=requested_by, sandbox_result=sandbox_result,
            oracle_verdict=oracle_result.verdict, oracle_detail=oracle_result.detail,
            started_at=started_at, completed_at=completed_at,
            model_id=generation.model_used, model_facts=self._model_response_facts(generation),
        )
        self._repo.create(run)
        self._record_event(
            run, "evalrun.generated_solution_completed",
            {
                "oracle_verdict": run.oracle_verdict.value,
                "sandbox_status": run.sandbox_status.value if run.sandbox_status else None,
                "model_id": run.model_id,
            },
        )
        self._record_audit(
            run, actor=requested_by.value, action="evalrun.generated_solution_completed",
            details={
                "task_id": task.id, "clone_id": clone.clone_id, "model_id": run.model_id,
                "oracle_verdict": run.oracle_verdict.value,
            },
        )
        return run

    @staticmethod
    def _model_response_facts(generation: Any) -> dict[str, Any]:
        """Sac `facts["model_response"]` — jamais mélangé aux clés déjà
        utilisées par le chemin sandbox (`memory_peak_mb`) ni par
        `strategy_facts` (`_build_eval_run`), même discipline de
        namespacing. `text` reste borné (`_MAX_STORED_RESPONSE_CHARS`,
        §10) — jamais une troncature silencieuse, `truncated` le dit
        toujours explicitement."""
        text = generation.text
        truncated = len(text) > _MAX_STORED_RESPONSE_CHARS
        if truncated:
            text = text[:_MAX_STORED_RESPONSE_CHARS]
        return {
            "model_response": {
                "text": text,
                "truncated": truncated,
                "model_used": generation.model_used,
                "finish_reason": generation.finish_reason,
                "tokens_prompt": (
                    generation.tokens_prompt.value
                    if generation.tokens_prompt.status is DataStatus.REAL
                    else None
                ),
                "tokens_completion": (
                    generation.tokens_completion.value
                    if generation.tokens_completion.status is DataStatus.REAL
                    else None
                ),
            }
        }

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
        model_id: str | None = None,
        model_facts: dict[str, Any] | None = None,
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
        # `model_facts` (passe « Generated-Solution Evaluation Loop », N+19)
        # — sac `model_response`, fusionné au même niveau que dans
        # `run_model_eval` (jamais imbriqué sous `strategy`, un namespace
        # distinct dédié à MetaLearner) : les deux chemins produisent le
        # même format `facts["model_response"]`, lisible identiquement.
        if model_facts:
            facts.update(model_facts)
        return EvalRun(
            id=eval_run_id, task_id=task.id, clone_id=clone_id, method=method,
            oracle_verdict=oracle_verdict, oracle_detail=oracle_detail,
            sandbox_status=sandbox_result.status, exit_code=exit_code, elapsed_s=elapsed_s,
            model_id=model_id, facts=facts, created_by=requested_by.value, seed=seed,
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
