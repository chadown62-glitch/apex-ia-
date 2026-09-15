"""`run_task_loop` — le moteur d'orchestration de tâche borné et observable
de la mission N+27 : OBJECTIVE -> PLAN -> PROVIDER SELECTION -> GENERATE
CANDIDATE -> EXECUTE -> TEST -> JUDGE -> SUCCESS (COMPLETE) ou FAILURE ->
`apex.recovery` (N+26) -> RETRY/REASSIGN/BLOCK/ESCALATE, borné par
`apex.taskloop.model.check_stop` ET par `apex.taskloop.progress.
detect_no_progress`.

**N+27 orchestre. N+26 récupère. N+28 recommande. Les composants existants
exécutent.** Ce module ne réimplémente RIEN de ce qui existe déjà — il
compose, dans l'ordre, exactement les primitives réelles suivantes, jamais
un second exemplaire :

- `apex.tasks.repository.TaskRepository` — lit une `Task` DÉJÀ persistée
  (TaskBank), jamais un objet Task fabriqué à la volée.
- `apex.routing.router.select_model` — sélection de provider, réelle,
  scorée, auditable (N+18). `run_task_loop` ne choisit JAMAIS un provider
  autrement que par cet appel.
- `apex.models.provider.ModelProvider.generate` — la même primitive que
  `apex.evalrunner.service.EvalRunnerService`/`apex.routing.generation.
  generate_with_fallback` appellent chacun pour leur propre domaine.
- `apex.evalrunner.generated_solution.build_candidate_command` — substitution
  du code généré dans le `solution_harness` de la `Task`, réutilisée telle
  quelle (N+19).
- `apex.agents.runtime.AgentRuntime.spawn`/`execute_in_sandbox` — exécution
  sandboxée réelle, AUCUN contournement (jamais de `--privileged`, jamais
  de socket Docker monté, jamais de dégradation silencieuse de l'isolation
  — `IsolationUnavailable` reste toujours une exception relevée, jamais
  absorbée en un succès dégradé).
- `apex.judge.provider.get_default_judge_tier().evaluate` — jugement
  evidence-based (Tier0Judge/`OracleProvider`, Phase 9), **jamais câblé nulle
  part avant cette mission** : c'est la pièce manquante que N+27 comble,
  pas une réimplémentation d'`apex.oracles`.
- `apex.recovery.decide`/`classify_sandbox_outcome`/`classify_provider_error`
  — la SEULE autorité de décision RETRY/REASSIGN/BLOCK/ESCALATE (N+26).
  Ce module ne décide JAMAIS lui-même d'une action de récupération — il
  l'applique.
- `apex.taskloop.model.check_stop` — les garde-fous obligatoires
  (annulation/échec d'infrastructure/indisponibilité provider priment
  toujours), posés par la préparation N+26 pour cette mission précisément.
- `apex.metalearner.experience.build_experience`/`ExperienceRepository` —
  chaque tentative produit une `Experience` RÉELLE, exploitable par
  `apex.metalearner.recommendation` (N+28) — jamais un second système de
  mémoire.

**Pourquoi ceci compose les primitives directement plutôt que d'appeler
`EvalRunnerService.run_generated_solution` en boîte noire** : ce dernier (1)
absorbe `ModelProviderError` dans un texte libre, perdant la catégorie
structurée (`ModelErrorCategory`) que `apex.recovery.classify_provider_error`
exige pour une classification evidence-based, et (2) ne prend qu'UN seul
`ModelEndpoint` fixe, sans support de réassignation — les deux capacités que
cette mission exige explicitement (§« Provider Selection », §« N+26
Recovery »). Composer directement les mêmes primitives qu'il compose
lui-même n'est pas une duplication : c'est exactement le patron déjà
appliqué par `apex.missions.execution.launch_mission`/`apex.evalrunner.
service.EvalRunnerService`/`apex.metalearner.generated_solution_cycle.
compare_generated_solutions`, chacun composant AgentRuntime/Sandbox/Oracle à
son propre niveau d'orchestration, jamais l'un par-dessus l'autre.

**Idempotence/concurrence** : `apex.taskloop.registry.TaskLoopRegistry`
(en mémoire, par-processus — voir sa docstring pour la limite honnête)
refuse un second `run_task_loop` concurrent pour la même `task_id`
(`TaskAlreadyRunning`) et borne le nombre de TaskLoop simultanés
(`TaskLoopAtCapacity`).

**Aucune reprise persistée** : un appel à `run_task_loop` est une exécution
complète, du premier attempt au résultat final — il n'existe aucune table
« TaskRun » permettant de reprendre une exécution interrompue à un état
arbitraire (§« Resume/Recovery » de la mission : « implémente uniquement le
minimum nécessaire »). Un TaskLoop interrompu (process tué) laisse
exactement la trace d'événements déjà émise, jamais plus — un futur appel
avec la même `task_id` est une tentative authentiquement nouvelle, jamais
une reprise devinée."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool, ExternalPolicy, RouterScoringPolicy
from apex.errors import IsolationUnavailable, ModelProviderError, TimeoutExceeded
from apex.evalrunner.generated_solution import build_candidate_command
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.judge.provider import get_default_judge_tier
from apex.metalearner.experience import ExperienceRepository, build_experience
from apex.metalearner.population_fitness import compute_fitness
from apex.models.model import GenerationRequest
from apex.models.provider import ModelProvider
from apex.models.provider import get_default_provider as get_default_model_provider
from apex.models.registry import ModelCandidate, filter_available, resolve_candidates
from apex.obs.status import DataStatus
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.recovery import (
    AttemptHistory,
    AttemptRecord,
    FailureCategory,
    RecoveryAction,
    classify_provider_error,
    classify_sandbox_outcome,
    decide,
)
from apex.routing.availability import probe_physical_availability
from apex.routing.model import RoutingRequest
from apex.routing.router import select_model
from apex.sandbox import ExecutionStatus, ResourceLimits, SandboxProvider, SandboxSpec
from apex.sandbox import get_default_provider as get_default_sandbox_provider
from apex.taskloop.model import (
    StopReason,
    TaskLoopStage,
    TaskLoopStopCriteria,
    assert_stage_transition_allowed,
    check_stop,
)
from apex.taskloop.progress import detect_no_progress
from apex.taskloop.registry import TaskLoopHandle, TaskLoopRegistry, default_registry
from apex.taskloop.request import TaskRequest
from apex.taskloop.result import TaskLoopStatus, TaskResult
from apex.tasks.repository import TaskRepository


def _default_provider_factory(candidate: ModelCandidate) -> ModelProvider:
    """Même patron qu'`apex.routing.generation._default_provider_factory` —
    injectable pour les tests (double de test fidèle au contrat
    `ModelProvider`, jamais un vrai modèle), `get_default_model_provider`
    par défaut."""
    assert candidate.endpoint is not None  # garanti par select_model (is_available filtré)
    return get_default_model_provider(candidate.endpoint)


#: Budget de génération par défaut — même ordre de grandeur que
#: `EvalRunnerService.run_generated_solution` (`generation_timeout_s=30.0`),
#: jamais réinventé sans preuve.
_DEFAULT_GENERATION_TIMEOUT_S = 30.0

#: Combien de dernières tentatives IDENTIQUES (même catégorie/provider/
#: stratégie) déclenchent NO_PROGRESS — voir `apex.taskloop.progress`.
_DEFAULT_NO_PROGRESS_PATIENCE = 2

#: Même valeur, même nom que `apex.evalrunner.service.
#: _MAX_STORED_CANDIDATE_OUTPUT_CHARS` — même discipline de troncature,
#: jamais réinventée. Voir `_persist_eval_run` : sans elle,
#: `EvalRun.facts` ne portait AUCUNE trace de `sandbox_result.stdout`/
#: `.stderr` (seuls `sandbox_result.facts`, propres au backend sandbox —
#: cgroup/conteneur —, y étaient copiés), rendant un `exit_code` non nul
#: (`FailureCategory.EXECUTION_FAILURE`) inexplicable après coup sans
#: ré-exécuter la tentative.
_MAX_STORED_CANDIDATE_OUTPUT_CHARS = 20_000

#: `FailureCategory` dont un `RecoveryAction.BLOCK` signale un problème
#: STRUCTUREL (jamais résolu en réessayant plus tard) -> `TaskLoopStatus.
#: BLOCKED`, distinct d'un `BLOCK` par simple épuisement de budget (->
#: `FAILED`, voir `_finalize_block`).
_STRUCTURAL_BLOCK_CATEGORIES = frozenset(
    {FailureCategory.SANDBOX_FAILURE, FailureCategory.PROVIDER_UNAVAILABLE}
)


def _advance_stage(current: TaskLoopStage, target: TaskLoopStage) -> TaskLoopStage:
    """Même discipline que `apex.missions.model.Mission.transition`/
    `apex.agents.model.Clone.transition` : jamais une mutation de l'état
    sans passer par `assert_stage_transition_allowed` — une transition
    absente de la table lève `InvalidTaskLoopTransition`, jamais absorbée."""
    assert_stage_transition_allowed(current, target)
    return target


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_limits(session_factory: Any) -> ResourceLimits:
    from apex.config.loader import load_config

    config = load_config()
    return ResourceLimits.from_config(config.sandbox)


def _record_task_event(
    session_factory: Any, *, trace_id: str, task_id: str, kind: str, details: dict[str, Any],
) -> None:
    """Même patron que `apex.missions.execution._record_mission_event`/
    `AgentRuntime._record_event` — jamais un second mécanisme de
    journalisation. `trace_id` retrouve la trace exclusive de CETTE
    exécution ; `task_id` reste présent pour filtrer toutes les exécutions
    d'une même `Task`."""
    from apex.db.engine import session_scope
    from apex.db.repositories import EventRepository

    with session_scope(session_factory) as session:
        EventRepository(session).add(
            ts=_utcnow(), level="INFO", logger="apex.taskloop",
            msg=kind, context={"trace_id": trace_id, "task_id": task_id, "kind": kind, **details},
        )


def run_task_loop(
    request: TaskRequest,
    *,
    session_factory: Any,
    agent_runtime: AgentRuntime,
    pool: ComputePool,
    task_repository: TaskRepository | None = None,
    candidates: tuple[ModelCandidate, ...] | None = None,
    routing_request: RoutingRequest | None = None,
    external_policy: ExternalPolicy | None = None,
    scoring_policy: RouterScoringPolicy | None = None,
    sandbox: SandboxProvider | None = None,
    limits: ResourceLimits | None = None,
    clone_subject: Subject = Subject.RESEARCH,
    experience_repo: ExperienceRepository | None = None,
    eval_run_repo: EvalRunRepository | None = None,
    registry: TaskLoopRegistry | None = None,
    provider_factory: Callable[[ModelCandidate], ModelProvider] = _default_provider_factory,
    generation_timeout_s: float = _DEFAULT_GENERATION_TIMEOUT_S,
    no_progress_patience: int = _DEFAULT_NO_PROGRESS_PATIENCE,
) -> TaskResult:
    """Exécute intégralement UNE tâche, du premier attempt au `TaskResult`
    final. Ne lève QUE pour des erreurs d'appel (tâche introuvable/mal
    formée, `TaskAlreadyRunning`, `TaskLoopAtCapacity`) — toute panne
    d'exécution réelle (sandbox, provider, jugement) est absorbée dans le
    `TaskResult` retourné, jamais une exception qui masquerait la trace
    déjà émise."""
    task_repo = task_repository or TaskRepository(session_factory)
    task = task_repo.get(request.task_id)
    if task is None:
        raise LookupError(f"tâche « {request.task_id} » introuvable")
    if task.solution_harness is None:
        raise ValueError(
            f"la tâche « {request.task_id} » ne porte pas de « solution_harness » — "
            "run_task_loop() exige une tâche de ce genre (voir apex.taskloop.request.TaskRequest)"
        )

    trace_id = str(uuid.uuid4())
    # `is None`, JAMAIS `registry or default_registry()` : `TaskLoopRegistry`
    # définit `__len__` (pour `len(registry)`, la vue de capacité) — un
    # registre valide mais VIDE (`len() == 0`, le cas normal au tout début
    # d'un test/process) serait alors faux au sens Python, substituant
    # silencieusement `default_registry()` au registre RÉELLEMENT fourni
    # par l'appelant. Même discipline appliquée aux autres paramètres
    # optionnels ci-dessous, par cohérence, même si eux ne portent
    # aujourd'hui aucun `__len__`/`__bool__` piégeux.
    active_registry = registry if registry is not None else default_registry()
    handle = TaskLoopHandle(task_id=request.task_id, trace_id=trace_id)
    active_registry.track(handle)
    try:
        return _run(
            request, task=task, trace_id=trace_id, cancel_token=handle.cancel_token,
            session_factory=session_factory, agent_runtime=agent_runtime, pool=pool,
            candidates=candidates, routing_request=routing_request, external_policy=external_policy,
            scoring_policy=scoring_policy,
            sandbox=sandbox if sandbox is not None else get_default_sandbox_provider(),
            limits=limits if limits is not None else _default_limits(session_factory),
            clone_subject=clone_subject,
            experience_repo=(
                experience_repo if experience_repo is not None else ExperienceRepository(session_factory)
            ),
            eval_run_repo=(
                eval_run_repo if eval_run_repo is not None else EvalRunRepository(session_factory)
            ),
            provider_factory=provider_factory,
            generation_timeout_s=generation_timeout_s, no_progress_patience=no_progress_patience,
        )
    finally:
        active_registry.discard(request.task_id)


def _run(
    request: TaskRequest, *, task: Any, trace_id: str, cancel_token: Any,
    session_factory: Any, agent_runtime: AgentRuntime, pool: ComputePool,
    candidates: tuple[ModelCandidate, ...] | None, routing_request: RoutingRequest | None,
    external_policy: ExternalPolicy | None, scoring_policy: RouterScoringPolicy | None,
    sandbox: SandboxProvider, limits: ResourceLimits, clone_subject: Subject,
    experience_repo: ExperienceRepository, eval_run_repo: EvalRunRepository,
    provider_factory: Callable[[ModelCandidate], ModelProvider],
    generation_timeout_s: float, no_progress_patience: int,
) -> TaskResult:
    def emit(kind: str, details: dict[str, Any]) -> None:
        _record_task_event(
            session_factory, trace_id=trace_id, task_id=request.task_id, kind=kind, details=details,
        )

    emit("task.created", {"objective": request.objective, "execution_mode": request.execution_mode.value})
    stage = _advance_stage(TaskLoopStage.OBJECTIVE, TaskLoopStage.TASK_GENERATION)

    # -- PLAN -----------------------------------------------------------
    emit("task.planning.started", {})
    prompt = task.description
    if request.context.strip():
        prompt = f"{prompt}\n\nContexte :\n{request.context}"
    if request.constraints.strip():
        prompt = f"{prompt}\n\nContraintes :\n{request.constraints}"
    emit("task.planning.completed", {})
    stage = _advance_stage(stage, TaskLoopStage.PLAN)

    # -- PROVIDER SELECTION (initiale) -----------------------------------
    from apex.config.loader import load_config

    resolved_candidates = (
        candidates if candidates is not None else filter_available(resolve_candidates(load_config()))
    )
    # Sonde la disponibilité PHYSIQUE (jamais seulement déclarative) avant
    # tout scoring — voir `apex.routing.availability` pour le diagnostic
    # exact du bug corrigé ici et pourquoi ni `apex.models.registry` ni
    # `apex.routing.router` (tous deux documentés purs) ne portent cette
    # sonde. `provider_factory` est réutilisé tel quel (même fabrique que
    # pour la génération plus bas) — aucun second mécanisme de construction
    # de `ModelProvider`.
    availability = probe_physical_availability(resolved_candidates, provider_factory=provider_factory)
    emit(
        "provider.availability_probe.completed",
        {
            "probed_endpoints": availability.probed_endpoints,
            "survivor_count": len(availability.survivors),
            "physically_unavailable": [
                {
                    "model_id": status.candidate.ref.id,
                    "backend": status.report.backend,
                    "detail": status.report.detail,
                }
                for status in availability.excluded
            ],
        },
    )
    resolved_routing_request = routing_request or RoutingRequest()
    emit("provider.selection.started", {"candidate_count": len(availability.survivors)})
    routing_decision = select_model(
        availability.survivors, resolved_routing_request,
        external_policy=external_policy, scoring_policy=scoring_policy,
    )
    eligible_candidates = tuple(
        sc.candidate for sc in routing_decision.ranked_candidates if sc.score > 0.0
    )
    if not eligible_candidates:
        emit("provider.selection.failed", {"reason": routing_decision.reason})
        decision = decide(
            FailureCategory.PROVIDER_UNAVAILABLE, attempt_number=1,
            policy=_effective_policy(request, load_config()), has_alternate_target=False,
            previous_failure=routing_decision.reason,
        )
        emit("recovery.decided", decision.to_dict())
        return _finalize(
            task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.BLOCKED, final_output=None,
            attempts=(), providers_tried=(), eval_run_ids=(), failure=routing_decision.reason,
            stop_reason="recovery_block", emit=emit,
        )
    emit(
        "provider.selected",
        {"model_id": eligible_candidates[0].ref.id, "reason": routing_decision.reason},
    )
    stage = _advance_stage(stage, TaskLoopStage.EXECUTION)

    effective_policy = _effective_policy(request, load_config())
    stop_criteria = TaskLoopStopCriteria(
        max_attempts=request.effective_max_attempts(),
        max_wall_clock_s=request.limits.max_wall_clock_s,
        resource_budget_description=(
            f"cpu_cores={limits.cpu_cores}, memory_mb={limits.memory_mb}, "
            f"timeout_s={limits.timeout_s}, network={limits.network}"
        ),
        success_criteria=lambda _r: False,
        failure_criteria=lambda _r: False,
    )

    history = AttemptHistory()
    providers_tried: list[str] = []
    eval_run_ids: list[str] = []
    candidate_index = 0
    current_candidate = eligible_candidates[0]
    retries_used = 0
    started_monotonic = time.monotonic()
    attempt_number = 0

    while True:
        # `attempt_number` porte ici le nombre de tentatives DÉJÀ terminées
        # (0 au premier tour) — même sémantique que l'exemple de la mission
        # N+27 (« attempt = 3, max_attempts = 3 -> STOP, jamais de tentative
        # 4 ») : `check_stop` doit voir le compte des tentatives ACHEVÉES,
        # jamais celui de la tentative sur le point de démarrer (un off-by-
        # one qui empêcherait à tort la dernière tentative autorisée).
        elapsed_s = time.monotonic() - started_monotonic
        stop_reason = check_stop(
            attempt_number=attempt_number, elapsed_s=elapsed_s, criteria=stop_criteria,
            cancel_token=cancel_token,
        )
        if stop_reason is StopReason.CANCELLED:
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.CANCELLED,
                final_output=None, attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                eval_run_ids=tuple(eval_run_ids), failure=None, stop_reason=stop_reason.value, emit=emit,
            )
        if stop_reason in (StopReason.MAX_ATTEMPTS_REACHED, StopReason.WALL_CLOCK_EXCEEDED):
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.FAILED, final_output=None,
                attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                eval_run_ids=tuple(eval_run_ids), failure=f"arrêt : {stop_reason.value}",
                stop_reason=stop_reason.value, emit=emit,
            )
        attempt_number += 1

        if current_candidate.ref.id not in providers_tried:
            providers_tried.append(current_candidate.ref.id)

        emit(
            "task.attempt.started",
            {"attempt_number": attempt_number, "model_id": current_candidate.ref.id},
        )
        clone = agent_runtime.spawn(
            clone_subject, pool=pool, genome_ref=f"taskloop:{trace_id}:{attempt_number}",
            spawned_by=request.authorization, mission_id=None,
        )
        attempt_started_at = _utcnow()
        attempt_started_monotonic = time.perf_counter()

        # -- GENERATE CANDIDATE -------------------------------------------
        emit(
            "candidate.generation.started",
            {"attempt_number": attempt_number, "model_id": current_candidate.ref.id},
        )
        assert current_candidate.endpoint is not None  # garanti par select_model (is_available filtré)
        generation_request = GenerationRequest(
            endpoint=current_candidate.endpoint, prompt=prompt, timeout_s=generation_timeout_s,
        )
        provider = provider_factory(current_candidate)
        try:
            generation = provider.generate(generation_request)
        except (ModelProviderError, TimeoutExceeded) as exc:
            duration_s = time.perf_counter() - attempt_started_monotonic
            category = (
                classify_provider_error(exc)
                if isinstance(exc, ModelProviderError)
                else FailureCategory.TIMEOUT
            )
            failure_text = str(exc)
            # Le clone n'a jamais atteint la sandbox — jamais laissé READY
            # orphelin : terminaison explicite, même mécanisme que tout
            # autre clone qui n'ira pas plus loin.
            agent_runtime.terminate(
                clone, requested_by=request.authorization,
                reason="génération échouée avant exécution sandbox",
            )
            outcome = _handle_failure(
                category=category, failure_text=failure_text, provider_name=current_candidate.ref.id,
                sandbox_level="n/a — génération jamais exécutée en sandbox", duration_s=duration_s,
                attempt_number=attempt_number, effective_policy=effective_policy, history=history,
                eligible_candidates=eligible_candidates, candidate_index=candidate_index,
                request=request, retries_used=retries_used, stage=stage,
                no_progress_patience=no_progress_patience, emit=emit,
            )
            stage = outcome.stage
            if outcome.terminal is not None:
                return _finalize(
                    task_id=request.task_id, trace_id=trace_id, status=outcome.terminal, final_output=None,
                    attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                    eval_run_ids=tuple(eval_run_ids), failure=failure_text, stop_reason=outcome.stop_reason,
                    emit=emit,
                )
            candidate_index = outcome.candidate_index
            current_candidate = eligible_candidates[candidate_index]
            retries_used = outcome.retries_used
            continue

        emit(
            "candidate.generated",
            {"attempt_number": attempt_number, "model_id": generation.model_used},
        )

        # -- EXECUTE -----------------------------------------------------
        command = build_candidate_command(
            solution_harness=task.solution_harness, candidate_code=generation.text,
        )
        eval_run_id = str(uuid.uuid4())
        spec = SandboxSpec(
            mission_id=eval_run_id, clone_id=clone.clone_id, command=command, limits=limits,
        )
        emit("execution.started", {"attempt_number": attempt_number, "clone_id": clone.clone_id})
        try:
            sandbox_result = agent_runtime.execute_in_sandbox(
                clone, sandbox, spec, requested_by=request.authorization, cancel_token=cancel_token,
            )
        except IsolationUnavailable as exc:
            duration_s = time.perf_counter() - attempt_started_monotonic
            category = classify_sandbox_outcome(isolation_unavailable=exc)
            failure_text = str(exc)
            outcome = _handle_failure(
                category=category, failure_text=failure_text, provider_name=current_candidate.ref.id,
                sandbox_level=sandbox.detect().level.value, duration_s=duration_s,
                attempt_number=attempt_number, effective_policy=effective_policy, history=history,
                eligible_candidates=eligible_candidates, candidate_index=candidate_index,
                request=request, retries_used=retries_used, stage=stage,
                no_progress_patience=no_progress_patience, emit=emit,
            )
            stage = outcome.stage
            if outcome.terminal is not None:
                return _finalize(
                    task_id=request.task_id, trace_id=trace_id, status=outcome.terminal, final_output=None,
                    attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                    eval_run_ids=tuple(eval_run_ids), failure=failure_text, stop_reason=outcome.stop_reason,
                    emit=emit,
                )
            candidate_index = outcome.candidate_index
            current_candidate = eligible_candidates[candidate_index]
            retries_used = outcome.retries_used
            continue

        emit(
            "execution.completed",
            {"attempt_number": attempt_number, "status": sandbox_result.status.value},
        )

        if sandbox_result.status is ExecutionStatus.CANCELLED:
            # Jamais interféré par la boucle de recovery — même discipline
            # que `apex.missions.execution.launch_mission`.
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.CANCELLED,
                final_output=None, attempts=tuple(history.records),
                providers_tried=tuple(providers_tried), eval_run_ids=tuple(eval_run_ids), failure=None,
                stop_reason=StopReason.CANCELLED.value, emit=emit,
            )

        # -- TEST / JUDGE --------------------------------------------------
        stage = _advance_stage(stage, TaskLoopStage.TEST)
        emit("test.started", {"attempt_number": attempt_number})
        verdict = get_default_judge_tier().evaluate(sandbox_result=sandbox_result, task=task)
        emit(
            "test.completed",
            {"attempt_number": attempt_number, "verdict": verdict.oracle_result.verdict.value},
        )
        stage = _advance_stage(stage, TaskLoopStage.JUDGE)
        emit("judge.started", {"attempt_number": attempt_number})
        emit(
            "judge.completed",
            {
                "attempt_number": attempt_number, "verdict": verdict.oracle_result.verdict.value,
                "detail": verdict.oracle_result.detail, "tier": verdict.tier.value,
            },
        )

        duration_s = time.perf_counter() - attempt_started_monotonic
        eval_run = _persist_eval_run(
            eval_run_id=eval_run_id, task=task, clone_id=clone.clone_id, sandbox_result=sandbox_result,
            oracle_verdict=verdict.oracle_result.verdict, oracle_detail=verdict.oracle_result.detail,
            model_id=generation.model_used, started_at=attempt_started_at, completed_at=_utcnow(),
            created_by=request.authorization.value, experiment_ref=trace_id, eval_run_repo=eval_run_repo,
        )
        eval_run_ids.append(eval_run.id)
        fitness = compute_fitness(eval_run, limits=limits)
        experience = build_experience(
            eval_run, fitness, strategy_id=f"candidate:{current_candidate.ref.id}",
            generation_number=0, generation_config={}, created_by=request.authorization.value,
        )
        experience_repo.create(experience)

        attempt_succeeded = (
            sandbox_result.status is ExecutionStatus.SUCCESS
            and verdict.oracle_result.verdict is OracleVerdict.PASS
        )
        if attempt_succeeded:
            stage = _advance_stage(stage, TaskLoopStage.COMPLETE)
            history.add(AttemptRecord(
                attempt_number=attempt_number, provider_name=current_candidate.ref.id,
                sandbox_level=sandbox_result.isolation_level.value, category=None, reason="succès",
                strategy=None, judge_result=verdict.oracle_result.verdict.value,
                started_at=attempt_started_at,
                duration_s=duration_s, reassigned=candidate_index > 0, decision=None,
            ))
            emit("task.attempt.completed", {"attempt_number": attempt_number, "outcome": "success"})
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.COMPLETED,
                final_output=sandbox_result.stdout, attempts=tuple(history.records),
                providers_tried=tuple(providers_tried), eval_run_ids=tuple(eval_run_ids), failure=None,
                stop_reason=StopReason.SUCCESS.value, emit=emit,
            )

        if sandbox_result.status is not ExecutionStatus.SUCCESS:
            category = classify_sandbox_outcome(result=sandbox_result)
        else:
            # La sandbox a réellement réussi, mais le Judge a jugé la
            # solution incorrecte — exactement le cas que N+26 avait câblé
            # et testé au niveau politique en attendant « un futur appelant
            # qui produirait cette catégorie » (voir `apex.recovery.model.
            # FailureCategory.INCORRECT_SOLUTION`) : c'est ce module.
            category = FailureCategory.INCORRECT_SOLUTION
        failure_text = (
            verdict.oracle_result.detail or sandbox_result.error_detail or sandbox_result.status.value
        )

        outcome = _handle_failure(
            category=category, failure_text=failure_text, provider_name=current_candidate.ref.id,
            sandbox_level=sandbox_result.isolation_level.value, duration_s=duration_s,
            attempt_number=attempt_number, effective_policy=effective_policy, history=history,
            eligible_candidates=eligible_candidates, candidate_index=candidate_index,
            request=request, retries_used=retries_used, stage=stage,
            no_progress_patience=no_progress_patience, emit=emit,
        )
        stage = outcome.stage
        emit("task.attempt.completed", {"attempt_number": attempt_number, "outcome": "failure"})
        if outcome.terminal is not None:
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=outcome.terminal, final_output=None,
                attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                eval_run_ids=tuple(eval_run_ids), failure=failure_text,
                stop_reason=outcome.stop_reason, emit=emit,
            )
        candidate_index = outcome.candidate_index
        current_candidate = eligible_candidates[candidate_index]
        retries_used = outcome.retries_used


def _effective_policy(request: TaskRequest, config: Any) -> Any:
    from dataclasses import replace

    return replace(config.recovery, max_attempts=request.effective_max_attempts())


class _AttemptOutcome:
    """Résultat interne de `_handle_failure` — soit un `terminal` (`status`
    final + `stop_reason`) à retourner immédiatement, soit un `candidate_index`/
    `retries_used` mis à jour pour la prochaine itération de la boucle
    (`RETRY`/`REASSIGN`). `stage` porte TOUJOURS l'étape `TaskLoopStage`
    réellement atteinte par cet appel — `EXECUTION` (prête pour la
    tentative suivante) pour `RETRY`/`REASSIGN`, `BLOCK` pour tout arrêt
    terminal (y compris `ESCALATE`, qui n'a pas d'étape dédiée dans la
    machine à états — voir `apex.taskloop.model`)."""

    __slots__ = ("candidate_index", "retries_used", "stage", "stop_reason", "terminal")

    def __init__(
        self, *, stage: TaskLoopStage, terminal: TaskLoopStatus | None = None, stop_reason: str = "",
        candidate_index: int = 0, retries_used: int = 0,
    ) -> None:
        self.terminal = terminal
        self.stop_reason = stop_reason
        self.candidate_index = candidate_index
        self.retries_used = retries_used
        self.stage = stage


def _handle_failure(
    *, category: FailureCategory, failure_text: str, provider_name: str, sandbox_level: str,
    duration_s: float, attempt_number: int, effective_policy: Any, history: AttemptHistory,
    eligible_candidates: tuple[ModelCandidate, ...], candidate_index: int, request: TaskRequest,
    retries_used: int, stage: TaskLoopStage, no_progress_patience: int, emit: Any,
) -> _AttemptOutcome:
    """Applique EXCLUSIVEMENT la décision d'`apex.recovery.decide` — ne
    décide JAMAIS elle-même d'une action de récupération. `no_progress`/
    `max_retries`/`max_candidates` sont des garde-fous SUPPLÉMENTAIRES,
    appliqués APRÈS `decide()`, qui ne font jamais que DURCIR une décision
    RETRY/REASSIGN en BLOCK — jamais l'inverse (jamais transformer un BLOCK
    en RETRY). `stage` (venant d'`EXECUTION` ou de `JUDGE` selon l'origine
    de l'échec — les deux autorisent `-> ANALYZE`, voir `apex.taskloop.
    model._STAGE_TRANSITIONS`) avance TOUJOURS via `_advance_stage`, jamais
    une simple affectation : une catégorie/décision qui produirait une
    transition absente de la table lèverait `InvalidTaskLoopTransition`
    ici, au plus tôt, jamais silencieusement absorbée."""
    remaining_candidates = eligible_candidates[candidate_index + 1 :]
    tried_distinct = candidate_index + 1
    has_alternate = bool(remaining_candidates) and tried_distinct < request.limits.max_candidates

    decision = decide(
        category, attempt_number=attempt_number, policy=effective_policy,
        has_alternate_target=has_alternate, previous_failure=failure_text,
    )
    history.add(AttemptRecord(
        attempt_number=attempt_number, provider_name=provider_name, sandbox_level=sandbox_level,
        category=category, reason=decision.reason, strategy=decision.strategy_change,
        judge_result=None, started_at=_utcnow(), duration_s=duration_s, reassigned=False,
        decision=decision.action,
    ))
    emit("recovery.started", {"attempt_number": attempt_number, "category": category.value})
    emit("recovery.decided", decision.to_dict())

    stage = _advance_stage(stage, TaskLoopStage.ANALYZE)
    stage = _advance_stage(stage, TaskLoopStage.STRATEGY_UPDATE)

    if decision.action in (RecoveryAction.RETRY, RecoveryAction.REASSIGN) and detect_no_progress(
        history, patience=no_progress_patience,
    ):
        stage = _advance_stage(stage, TaskLoopStage.BLOCK)
        return _AttemptOutcome(stage=stage, terminal=TaskLoopStatus.FAILED, stop_reason="no_progress")

    if decision.action is RecoveryAction.RETRY:
        if retries_used >= request.limits.max_retries:
            stage = _advance_stage(stage, TaskLoopStage.BLOCK)
            return _AttemptOutcome(
                stage=stage, terminal=TaskLoopStatus.FAILED, stop_reason="max_retries_exceeded",
            )
        emit("retry.started", {"attempt_number": attempt_number, "next_attempt_number": attempt_number + 1})
        stage = _advance_stage(stage, TaskLoopStage.RETRY)
        stage = _advance_stage(stage, TaskLoopStage.PLAN)
        stage = _advance_stage(stage, TaskLoopStage.EXECUTION)
        return _AttemptOutcome(stage=stage, candidate_index=candidate_index, retries_used=retries_used + 1)

    if decision.action is RecoveryAction.REASSIGN:
        if not has_alternate:  # garde-fou — decide() ne devrait pas avoir choisi REASSIGN sinon
            stage = _advance_stage(stage, TaskLoopStage.BLOCK)
            return _AttemptOutcome(
                stage=stage, terminal=TaskLoopStatus.BLOCKED, stop_reason="recovery_block",
            )
        next_index = candidate_index + 1
        emit(
            "reassignment.decided",
            {
                "from_model_id": eligible_candidates[candidate_index].ref.id,
                "to_model_id": eligible_candidates[next_index].ref.id,
            },
        )
        stage = _advance_stage(stage, TaskLoopStage.REASSIGN)
        stage = _advance_stage(stage, TaskLoopStage.PLAN)
        stage = _advance_stage(stage, TaskLoopStage.EXECUTION)
        return _AttemptOutcome(stage=stage, candidate_index=next_index, retries_used=retries_used)

    if decision.action is RecoveryAction.ESCALATE:
        emit("recovery.escalated", decision.to_dict())
        stage = _advance_stage(stage, TaskLoopStage.BLOCK)
        return _AttemptOutcome(
            stage=stage, terminal=TaskLoopStatus.FAILED, stop_reason="recovery_escalated",
        )

    # RecoveryAction.BLOCK
    status = TaskLoopStatus.BLOCKED if category in _STRUCTURAL_BLOCK_CATEGORIES else TaskLoopStatus.FAILED
    stage = _advance_stage(stage, TaskLoopStage.BLOCK)
    return _AttemptOutcome(stage=stage, terminal=status, stop_reason="recovery_block")


def _persist_eval_run(
    *, eval_run_id: str, task: Any, clone_id: str, sandbox_result: Any, oracle_verdict: OracleVerdict,
    oracle_detail: str | None, model_id: str, started_at: datetime, completed_at: datetime,
    created_by: str, experiment_ref: str, eval_run_repo: EvalRunRepository,
) -> EvalRun:
    exit_code = (
        int(sandbox_result.exit_code.value) if sandbox_result.exit_code.status is DataStatus.REAL else None
    )
    elapsed_s = (
        float(sandbox_result.elapsed_s.value)
        if sandbox_result.elapsed_s.status is DataStatus.REAL
        else None
    )
    # `sandbox_result.facts` seul (cgroup/conteneur, propre au backend
    # sandbox) ne porte jamais stdout/stderr — même clé/troncature que
    # `apex.evalrunner.service.EvalRunnerService._candidate_execution_facts`,
    # jamais un second format. Fusionné, jamais remplacé : aucune clé
    # `"candidate_execution"` n'est produite par un backend sandbox
    # aujourd'hui (`apex/sandbox/linux_namespace.py`/`container.py`
    # vérifiés) — pas de collision possible.
    facts = dict(sandbox_result.facts)
    stdout = sandbox_result.stdout
    stderr = sandbox_result.stderr
    facts["candidate_execution"] = {
        "stdout": stdout[:_MAX_STORED_CANDIDATE_OUTPUT_CHARS],
        "stdout_truncated": (
            sandbox_result.stdout_truncated or len(stdout) > _MAX_STORED_CANDIDATE_OUTPUT_CHARS
        ),
        "stderr": stderr[:_MAX_STORED_CANDIDATE_OUTPUT_CHARS],
        "stderr_truncated": (
            sandbox_result.stderr_truncated or len(stderr) > _MAX_STORED_CANDIDATE_OUTPUT_CHARS
        ),
    }
    run = EvalRun(
        id=eval_run_id, task_id=task.id, method=EvalMethod.EXPERIMENTATION, oracle_verdict=oracle_verdict,
        oracle_detail=oracle_detail, exit_code=exit_code, elapsed_s=elapsed_s,
        facts=facts, created_by=created_by, clone_id=clone_id,
        sandbox_status=sandbox_result.status, model_id=model_id, experiment_ref=experiment_ref,
        started_at=started_at, completed_at=completed_at,
    )
    eval_run_repo.create(run)
    return run


def _finalize(
    *, task_id: str, trace_id: str, status: TaskLoopStatus, final_output: str | None,
    attempts: tuple[AttemptRecord, ...], providers_tried: tuple[str, ...], eval_run_ids: tuple[str, ...],
    failure: str | None, stop_reason: str, emit: Any,
) -> TaskResult:
    kind = {
        TaskLoopStatus.COMPLETED: "task.completed",
        TaskLoopStatus.FAILED: "task.failed",
        TaskLoopStatus.BLOCKED: "task.blocked",
        TaskLoopStatus.CANCELLED: "task.cancelled",
    }[status]
    emit(kind, {"stop_reason": stop_reason, "attempt_count": len(attempts)})
    return TaskResult(
        task_id=task_id, trace_id=trace_id, status=status, final_output=final_output, attempts=attempts,
        providers_tried=providers_tried, eval_run_ids=eval_run_ids, failure=failure,
        stop_reason=stop_reason,
    )
