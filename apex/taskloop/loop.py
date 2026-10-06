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

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool, ExternalPolicy, RouterScoringPolicy
from apex.errors import IsolationUnavailable, ModelProviderError, TimeoutExceeded
from apex.evalrunner.generated_solution import build_candidate_command, ensure_candidate_marker
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.genome.clone_config import compose_generation_prompt, resolve_generation_temperature
from apex.judge.provider import get_default_judge_tier
from apex.experience.store import ExperienceRepository, build_experience
from apex.experience.fitness import compute_fitness
from apex.models.model import GenerationRequest
from apex.models.provider import ModelProvider
from apex.models.provider import get_default_provider as get_default_model_provider
from apex.models.registry import ModelCandidate, filter_available, resolve_candidates
from apex.obs.status import DataStatus
from apex.oracles.model import OracleVerdict
from apex.oracles.result_contract import declares_result_contract, read_published_results
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
from apex.recovery.classify import classify_execution_status
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
from apex.tasks.model import TaskKind
from apex.tasks.repository import TaskRepository


#: Classes de modèles qui GÉNÈRENT le code d'une tâche. Exclues (audit du 03/10/2026, machine cible où
#: `bge-m3` et `gemma4:12b` sont installés) : `embed` (un modèle d'embedding ne génère jamais de texte —
#: une réassignation vers lui ne pouvait qu'échouer) et `judge` (principe 2 de config.yaml : le juge reste
#: d'une famille distincte du générateur ; il ne doit jamais devenir générateur par réassignation).
CODE_GENERATION_CLASSES = frozenset({"coder", "fast", "generalist"})


def code_generation_candidates(config: Any) -> tuple[ModelCandidate, ...]:
    return tuple(
        c for c in filter_available(resolve_candidates(config)) if c.ref.capability_class in CODE_GENERATION_CLASSES
    )


@dataclass(frozen=True)
class _Strategy:
    """Stratégie de génération d'une TaskRun et sa provenance. Non conditionnée : aucun contenu, prompt et
    température du fournisseur inchangés (comportement historique, bras « baseline »)."""

    conditioned: bool
    genome_content: dict[str, Any] | None = None
    description: str | None = None
    source: Any = None  # `Experience` dont dérive la stratégie, ou None
    read_count: int = 0

    @property
    def generation_number(self) -> int:
        return (self.source.generation_number + 1) if self.source is not None else 0

    def event(self) -> dict[str, Any]:
        return {
            "experiences_read": self.read_count,
            "source_experience_id": self.source.id if self.source else None,
            "source_failure_category": self.source.failure_category.value if self.source else None,
            "genome_content": self.genome_content or {},
            "description": self.description,
        }


def _base_genome(request: TaskRequest) -> dict[str, Any] | None:
    """Température de base EXPLICITE (RUN 1, bras A et C du protocole v2) ; `None` : celle du fournisseur."""
    if request.base_temperature is None:
        return None
    return {"generation": {"temperature": request.base_temperature}}


def _strategy_for(
    request: TaskRequest, *, experience_repo: ExperienceRepository, eval_run_repo: EvalRunRepository
) -> _Strategy:
    """Lit les `Experience` de la TaskRun `request.condition_on_run` (MÊME tâche — filtre `task_id` ET
    `experiment_ref`, jamais une autre tâche), puis :
    - `strategy="rule"` (bras B) : règle EXISTANTE `decide_strategy_from_experiences`, inchangée — seuls des
      contenus constants et bornés en sortent (température, rappel générique ≤ 500 caractères) ;
    - `strategy="failure_facts"` (bras C) : température de base + faits d'échec AUTORISÉS seuls, rendus comme
      des observations (`apex.experience.failure_facts`, liste fermée).
    Jamais d'entrée ni de réponse des cas cachés, de message d'exception, ni de texte produit par un modèle."""
    if not request.condition_on_run:
        return _Strategy(conditioned=False, genome_content=_base_genome(request))
    prior = [
        e for e in experience_repo.list_by_task(request.task_id, limit=500)
        if e.experiment_ref == request.condition_on_run
    ]
    if request.strategy == "failure_facts":
        return _failure_facts_strategy(request, prior, eval_run_repo=eval_run_repo)
    from apex.experience.strategy import decide_strategy_from_experiences
    from apex.experience.strategy import CandidateStrategy

    decision = decide_strategy_from_experiences(prior, base_strategies=(CandidateStrategy(label="base"),))
    return _Strategy(
        conditioned=True, genome_content=decision.strategies[0].genome_content,
        description=decision.description, source=decision.source, read_count=len(prior),
    )


def _failure_facts_strategy(
    request: TaskRequest, prior: list[Any], *, eval_run_repo: EvalRunRepository
) -> _Strategy:
    from apex.experience.failure_facts import PROMPT_VERSION, extract_failure_facts, render_failure_facts

    if not prior:
        return _Strategy(conditioned=True, genome_content=_base_genome(request),
                         description="bras C : aucune expérience trouvée — aucun fait transmis", read_count=0)
    source = max(prior, key=lambda e: e.created_at)  # la dernière tentative jugée du RUN précédent
    eval_run = eval_run_repo.get(source.eval_run_id)
    facts = extract_failure_facts(eval_run, source.failure_category) if eval_run is not None else None
    if facts is None:
        return _Strategy(
            conditioned=True, genome_content=_base_genome(request), source=source, read_count=len(prior),
            description=f"bras C : catégorie « {source.failure_category.value} » non couverte — aucun fait transmis",
        )
    generation = {"prompt_suffix": render_failure_facts(facts), "temperature": request.base_temperature}
    return _Strategy(
        conditioned=True, source=source, read_count=len(prior),
        genome_content={
            "generation": generation, "prompt_version": PROMPT_VERSION,
            "failure_facts": facts.to_dict(), "source_eval_run_id": source.eval_run_id,
        },
        description=f"bras C : faits d'échec structurés ({PROMPT_VERSION}), sans consigne",
    )


def _adapted_strategy(current: _Strategy, experience: Any, eval_run: EvalRun, attempt_number: int) -> _Strategy | None:
    """DIAGNOSTIC -> CORRECTION entre deux tentatives d'une même TaskRun : la tentative suivante reçoit les
    faits d'échec AUTORISÉS de celle qui vient d'échouer (`apex.experience.failure_facts` : liste fermée,
    observations sans consigne, jamais entrée cachée, réponse attendue, message d'exception ni code).
    La température en cours est conservée. `None` : aucun fait exploitable (catégorie non couverte, ex.
    délai dépassé) — la tentative suivante garde la stratégie en cours, et l'événement le dit."""
    from apex.experience.failure_facts import PROMPT_VERSION, extract_failure_facts, render_failure_facts

    facts = extract_failure_facts(eval_run, experience.failure_category)
    if facts is None:
        return None
    genome = dict(current.genome_content or {})
    generation = dict(genome.get("generation") or {})
    generation["prompt_suffix"] = render_failure_facts(facts)
    genome.update(
        generation=generation, prompt_version=PROMPT_VERSION, failure_facts=facts.to_dict(),
        source_eval_run_id=eval_run.id, adaptation="in_run",
    )
    return _Strategy(
        conditioned=True, genome_content=genome, source=experience, read_count=1,
        description=(
            f"tentative {attempt_number + 1} : faits d'échec de la tentative {attempt_number} "
            f"({PROMPT_VERSION}), sans consigne"
        ),
    )


_PLAN_MAX_CHARS = 2000
_PLAN_HEADER = "Analyse et plan rédigés à l'étape précédente (à suivre s'ils sont justes) :"
_PLAN_INSTRUCTION = (
    "Avant d'écrire le moindre code, analyse le problème ci-dessous. Réponds en français, en 12 lignes au "
    "plus : (1) ce que la fonction doit faire exactement ; (2) les contraintes et cas limites de l'énoncé ; "
    "(3) les étapes de l'algorithme. N'écris AUCUN code.\n\nProblème :\n"
)


#: Mesuré le 06/10/2026 (validation réelle `real-2`, docs/VALIDATION_REELLE.md §3) : malgré
#: « N'écris AUCUN code », le modèle mettait du code dans 58 plans sur 60 ; réinjecté, ce code (parfois
#: contraire à l'énoncé) était ensuite COMMENTÉ en prose au lieu d'une solution. Le contrat « analyse sans
#: code » est donc appliqué : les blocs ``` sont retirés du plan avant réinjection (le texte brut reste
#: journalisé), et la consigne de format est rappelée EN DERNIER, après le plan et les faits d'échec (hors
#: mode outillé, qui a son propre protocole).
_PLAN_CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_PLAN_CLOSING = (
    "Fin de l'analyse. Écris maintenant la solution : réponds UNIQUEMENT avec le code Python complet "
    "demandé par l'énoncé (aucune explication, aucun commentaire sur l'analyse)."
)


def _plan_without_code(plan: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _PLAN_CODE_BLOCK.sub("", plan)).strip()


def _compose_prompt(prompt: str, genome_content: dict[str, Any] | None, *, plan_closing: bool) -> str:
    text = compose_generation_prompt(prompt, genome_content)
    return f"{text}\n\n{_PLAN_CLOSING}" if plan_closing else text


def _generate_plan(
    prompt: str, *, candidate: ModelCandidate, provider_factory: Callable[[ModelCandidate], ModelProvider],
    temperature: float | None, timeout_s: float, emit: Callable[[str, dict[str, Any]], None],
) -> str | None:
    """Étape PLAN RÉELLE : un appel au modèle sélectionné, borné en temps et en taille, dont le texte est
    journalisé tel quel. Aucun code n'est exécuté ici. Un échec n'arrête jamais la tâche : il est journalisé
    (`task.plan.failed`) et la génération part de l'énoncé seul — jamais un plan inventé."""
    assert candidate.endpoint is not None
    started = time.perf_counter()
    try:
        result = provider_factory(candidate).generate(GenerationRequest(
            endpoint=candidate.endpoint, prompt=_PLAN_INSTRUCTION + prompt, temperature=temperature,
            timeout_s=timeout_s,
        ))
    except (ModelProviderError, TimeoutExceeded) as exc:
        emit("task.plan.failed", {"model_id": candidate.ref.id, "reason": str(exc)[:500]})
        return None
    text = (result.text or "").strip()
    if not text:
        emit("task.plan.failed", {"model_id": candidate.ref.id, "reason": "réponse vide"})
        return None
    analysis = _plan_without_code(text)
    code_removed = _PLAN_CODE_BLOCK.search(text) is not None
    if not analysis:
        emit("task.plan.failed", {"model_id": candidate.ref.id,
                                  "reason": "le plan ne contenait que du code",
                                  "raw_plan": text[:_PLAN_MAX_CHARS]})
        return None
    truncated = len(analysis) > _PLAN_MAX_CHARS
    analysis = analysis[:_PLAN_MAX_CHARS]
    emit("task.plan.generated", {
        "model_id": result.model_used, "plan": analysis, "truncated": truncated,
        "code_removed": code_removed, "raw_plan": text[:_PLAN_MAX_CHARS] if code_removed else None,
        "elapsed_s": round(time.perf_counter() - started, 3),
    })
    return analysis


_MEMORY_HEADER = (
    "Mémoire pertinente d'APEX (expériences et connaissances passées, avec leur source — à utiliser seulement "
    "si elles s'appliquent à cette tâche) :"
)


def _recall(
    request: TaskRequest, *, task: Any, trace_id: str, prompt: str, session_factory: Any,
    emit: Callable[[str, dict[str, Any]], None],
) -> tuple[str, list[str]]:
    """RETRIEVAL -> contexte : les souvenirs système les plus pertinents pour l'énoncé (jamais toute la
    mémoire), provenance comprise. Une mémoire indisponible n'arrête jamais la tâche (`memory.unavailable`)."""
    from apex.memory.model import MemoryCategory
    from apex.memory.retrieval import render_context
    from apex.memory.service import MemoryService

    from apex.tasks.solution_tasks import retrieval_statement

    try:
        items = MemoryService(session_factory).retrieve(
            retrieval_statement(task.description), requested_by=request.authorization,
            categories=(MemoryCategory.EXECUTION, MemoryCategory.KNOWLEDGE, MemoryCategory.PROJECT),
            exclude_source_prefixes=(f"task_run:{trace_id}", f"workspace:{trace_id}:"),
        )
    except Exception as exc:
        emit("memory.unavailable", {"error": f"{exc.__class__.__name__}: {exc}"[:300]})
        return prompt, []
    emit("memory.retrieved", {"items": [
        {"id": i.record.id, "category": i.record.category.value if i.record.category else None,
         "source": i.record.source, "score": round(i.score, 4)} for i in items]})
    if not items:
        return prompt, []
    return f"{prompt}\n\n{render_context(items, header=_MEMORY_HEADER)}", [i.record.id for i in items]


#: Échecs JUGÉS imputables au code produit (jamais une panne de fournisseur ou d'infrastructure).
_COMPETENCE_FAILURES = frozenset({"incorrect_solution", "execution_failure", "invalid_candidate", "timeout"})
_ROUTING_MIN_EVIDENCE = 2


def _experience_routing(
    request: TaskRequest, *, task: Any, trace_id: str, eligible: tuple[ModelCandidate, ...],
    session_factory: Any, emit: Callable[[str, dict[str, Any]], None],
) -> tuple[ModelCandidate, ...]:
    """EXPÉRIENCE -> RÉCUPÉRATION PERTINENTE -> DÉCISION : l'ordre d'essai des modèles éligibles est ajusté
    d'après les exécutions PASSÉES de tâches SIMILAIRES (mémoire système, BM25 sur l'énoncé) — pour chaque
    modèle, tentatives validées par le Judge moins échecs jugés imputables au code. Règle explicite et
    déterministe (tri stable, aucun modèle retiré, rien sans au moins `_ROUTING_MIN_EVIDENCE` tentatives
    observées), journalisée avec ses preuves. C'est une heuristique de décision, pas un apprentissage : son
    effet sur la réussite n'est pas mesuré ici."""
    if len(eligible) < 2:
        return eligible
    from collections import Counter

    from apex.memory.model import MemoryCategory
    from apex.memory.service import MemoryService
    from apex.taskloop.runs import TaskRunRepository

    from apex.tasks.solution_tasks import retrieval_statement

    try:
        items = MemoryService(session_factory).retrieve(
            retrieval_statement(task.description), requested_by=request.authorization,
            categories=(MemoryCategory.EXECUTION,),
            limit=10, exclude_source_prefixes=(f"task_run:{trace_id}",),
        )
    except Exception as exc:
        emit("routing.experience_unavailable", {"error": f"{exc.__class__.__name__}: {exc}"[:300]})
        return eligible
    runs = TaskRunRepository(session_factory)
    wins: Counter[str] = Counter()
    losses: Counter[str] = Counter()
    evidence = []
    for item in items:
        if not item.record.source.startswith("task_run:"):
            continue
        past = runs.get(item.record.source.split(":", 1)[1])
        if past is None:
            continue
        evidence.append({"task_run_id": past.id, "score": round(item.score, 4)})
        for attempt in past.attempts:
            name = str(attempt.get("provider_name"))
            if attempt.get("judge_result") == "pass":
                wins[name] += 1
            elif attempt.get("category") in _COMPETENCE_FAILURES:
                losses[name] += 1
    per_candidate = {c.ref.id: {"pass": wins[c.ref.id], "fail": losses[c.ref.id]} for c in eligible}
    observed = sum(v["pass"] + v["fail"] for v in per_candidate.values())
    if observed < _ROUTING_MIN_EVIDENCE:
        emit("routing.experience_insufficient", {"observed_attempts": observed, "evidence": evidence})
        return eligible
    ranked = tuple(sorted(eligible, key=lambda c: -(wins[c.ref.id] - losses[c.ref.id])))
    emit("routing.experience_adjusted" if ranked != eligible else "routing.experience_confirmed", {
        "before": [c.ref.id for c in eligible], "after": [c.ref.id for c in ranked],
        "per_candidate": per_candidate, "evidence": evidence,
        "rule": "tri stable par (validées - échecs jugés) sur les exécutions similaires",
    })
    return ranked


@dataclass(frozen=True)
class _ToolSetup:
    workspace: Any
    engine: Any
    granted: frozenset[Any]


def _tool_setup(
    request: TaskRequest, *, task: Any, trace_id: str, workspace_root: str | None,
    emit: Callable[[str, dict[str, Any]], None],
) -> _ToolSetup | None:
    """Espace de travail de la TaskRun (`<paths.workspaces>/<trace_id>`) et moteur d'outils — seulement si la
    requête accorde des permissions. Le même espace sert à toutes les tentatives (continuité du travail)."""
    if not request.tool_permissions:
        return None
    from apex.tools.builtin import default_registry
    from apex.tools.engine import ToolEngine
    from apex.tools.model import Permission
    from apex.tools.workspace import Workspace

    if workspace_root is None:
        from apex.config.loader import load_config

        workspace_root = load_config().paths.workspaces
    workspace = Workspace.create(workspace_root, trace_id)
    seeded = 0
    if task.workspace_seed and not workspace.files():
        # Projet de départ VISIBLE : déposé une seule fois (un espace déjà rempli n'est jamais écrasé).
        for path, content in sorted(task.workspace_seed.items()):
            workspace.write_bytes(path, content.encode("utf-8"))
            seeded += 1
    granted = frozenset(Permission(p) for p in request.tool_permissions)
    engine = ToolEngine(default_registry())
    emit("workspace.created", {
        "workspace_id": workspace.id, "granted": sorted(p.value for p in granted), "seeded_files": seeded,
        "tools": [spec.name for spec in engine.registry.available_to(granted)],
    })
    return _ToolSetup(workspace=workspace, engine=engine, granted=granted)


def _run_episode(
    tools: _ToolSetup, *, request: TaskRequest, provider: ModelProvider, generation_request: GenerationRequest,
    agent_runtime: AgentRuntime, sandbox: SandboxProvider, pool: ComputePool, limits: ResourceLimits,
    clone_subject: Subject, trace_id: str, attempt_number: int, cancel_token: Any, deadline: float,
    emit: Callable[[str, dict[str, Any]], None], task: Any,
) -> Any:
    from apex.taskloop.agent import run_tool_episode
    from apex.tools.engine import ToolContext
    from apex.tools.sandbox_runner import sandbox_runner

    context = ToolContext(
        workspace=tools.workspace, granted=tools.granted, emit=emit,
        run_in_sandbox=sandbox_runner(
            agent_runtime=agent_runtime, sandbox=sandbox, pool=pool, base_limits=limits,
            requested_by=request.authorization, clone_subject=clone_subject,
            label=f"taskloop:{trace_id}:{attempt_number}:tool", cancel_token=cancel_token,
        ),
    )
    project = task.effective_kind is TaskKind.PROJECT
    generation, _episode = run_tool_episode(
        task_prompt=generation_request.prompt, project=project, generate=provider.generate, endpoint=generation_request.endpoint,
        temperature=generation_request.temperature, timeout_s=generation_request.timeout_s,
        engine=tools.engine, context=context, max_steps=request.max_tool_steps, deadline_monotonic=deadline,
        cancel_requested=lambda: bool(getattr(cancel_token, "is_cancel_requested", False)),
        requested_by=f"taskloop:{trace_id}:{attempt_number}", emit=emit,
    )
    return generation


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


def _judged_failure_category(task: Any, stdout: str) -> FailureCategory:
    """Sandbox terminée normalement mais solution non acceptée par le Judge.
    Sous le contrat de résultat (`apex.oracles.result_contract`), le script
    ne sort plus en erreur sur une mauvaise réponse : la cause se lit dans
    les résultats publiés — aucun résultat ou contrat violé -> candidat
    invalide ; exception levée par le candidat -> échec d'exécution (même
    catégorie qu'avant le contrat) ; résultats bien formés mais faux ->
    INCORRECT_SOLUTION."""
    if not declares_result_contract(task):
        return FailureCategory.INCORRECT_SOLUTION
    published = read_published_results(stdout)
    if published.results is None:
        return FailureCategory.INVALID_CANDIDATE
    if any(isinstance(r, dict) and "error" in r for r in published.results):
        return FailureCategory.EXECUTION_FAILURE
    return FailureCategory.INCORRECT_SOLUTION


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
    trace_id: str | None = None,
    workspace_root: str | None = None,
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
    if task.effective_kind is TaskKind.PROJECT:
        if "workspace.write" not in request.tool_permissions:
            raise ValueError(
                f"la tâche « {request.task_id} » est un projet : elle exige le mode outillé "
                "(tool_permissions avec « workspace.write »)"
            )
    else:
        ensure_candidate_marker(task.solution_harness)

    # `trace_id` fourni par `apex.taskloop.runs.TaskRunService` : la TaskRun persistée porte la MÊME clé
    # que les EvalRun (`experiment_ref`) et les événements de cette exécution.
    trace_id = trace_id or str(uuid.uuid4())
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
            workspace_root=workspace_root,
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
    generation_timeout_s: float, no_progress_patience: int, workspace_root: str | None = None,
) -> TaskResult:
    def emit(kind: str, details: dict[str, Any]) -> None:
        _record_task_event(
            session_factory, trace_id=trace_id, task_id=request.task_id, kind=kind, details=details,
        )

    emit("task.created", {"objective": request.objective, "execution_mode": request.execution_mode.value})
    stage = _advance_stage(TaskLoopStage.OBJECTIVE, TaskLoopStage.TASK_GENERATION)

    # -- PLAN -----------------------------------------------------------
    # Ce que l'étape PLAN fait RÉELLEMENT est dit dans l'événement : `prompt_composition` (énoncé, contexte,
    # contraintes) ou, avec `plan_first`, une analyse rédigée par le modèle (plus bas, après la sélection).
    planning_mode = "model_plan" if request.plan_first else "prompt_composition"
    emit("task.planning.started", {"mode": planning_mode})
    prompt = task.description
    if request.context.strip():
        prompt = f"{prompt}\n\nContexte :\n{request.context}"
    if request.constraints.strip():
        prompt = f"{prompt}\n\nContraintes :\n{request.constraints}"
    emit("task.planning.completed", {"mode": planning_mode})

    # -- MÉMOIRE (souvenirs pertinents, sur demande) ------------------------------------------------
    memory_ids: list[str] = []
    if request.use_memory:
        prompt, memory_ids = _recall(request, task=task, trace_id=trace_id, prompt=prompt,
                                     session_factory=session_factory, emit=emit)
    stage = _advance_stage(stage, TaskLoopStage.PLAN)

    # -- STRATÉGIE (expérience d'un RUN précédent, si demandé) ---------------
    strategy = _strategy_for(request, experience_repo=experience_repo, eval_run_repo=eval_run_repo)
    if memory_ids:
        from dataclasses import replace as _replace_strategy

        strategy = _replace_strategy(
            strategy, genome_content={**(strategy.genome_content or {}), "memory": memory_ids},
        )
    if strategy.conditioned:
        emit("strategy.decided", {"condition_on_run": request.condition_on_run, **strategy.event()})
    plan_closing = False  # consigne de format rappelée en dernier : seulement avec un plan injecté
    generation_prompt = _compose_prompt(prompt, strategy.genome_content, plan_closing=plan_closing)
    generation_temperature = resolve_generation_temperature(strategy.genome_content)

    # -- PROVIDER SELECTION (initiale) -----------------------------------
    from apex.config.loader import load_config

    resolved_candidates = candidates if candidates is not None else code_generation_candidates(load_config())
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
    if request.use_memory:
        eligible_candidates = _experience_routing(
            request, task=task, trace_id=trace_id, eligible=eligible_candidates,
            session_factory=session_factory, emit=emit,
        )
    emit(
        "provider.selected",
        {"model_id": eligible_candidates[0].ref.id, "reason": routing_decision.reason},
    )

    # -- PLAN par le modèle (optionnel) : analyse sans code, réinjectée dans la génération -------------
    if request.plan_first:
        if request.resume and request.resume_plan:
            # Reprise : le plan rédigé AVANT l'interruption est réutilisé tel quel (aucun nouvel appel).
            plan = request.resume_plan
            emit("task.plan.reused", {"plan": plan})
        else:
            plan = _generate_plan(
                prompt, candidate=eligible_candidates[0], provider_factory=provider_factory,
                temperature=generation_temperature, timeout_s=generation_timeout_s, emit=emit,
            )
        if plan is not None:
            prompt = f"{prompt}\n\n{_PLAN_HEADER}\n{plan}"
            plan_closing = not request.tool_permissions
            from dataclasses import replace as _replace

            strategy = _replace(strategy, genome_content={**(strategy.genome_content or {}), "plan": plan})
            generation_prompt = _compose_prompt(prompt, strategy.genome_content, plan_closing=plan_closing)
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

    # -- OUTILS (mode outillé, permissions explicitement accordées par la requête) ----------------
    tools = _tool_setup(request, task=task, trace_id=trace_id, workspace_root=workspace_root, emit=emit)

    history = AttemptHistory()
    providers_tried: list[str] = []
    eval_run_ids: list[str] = []
    candidate_index = 0
    current_candidate = eligible_candidates[0]
    retries_used = 0
    started_monotonic = time.monotonic()
    attempt_number = 0

    # -- REPRISE (TaskRun interrompue) : l'état persisté est relu, la boucle CONTINUE ---------------------
    if request.resume:
        prior = _judged_attempts(request, trace_id=trace_id, experience_repo=experience_repo,
                                 eval_run_repo=eval_run_repo)
        emit("task.resumed", {
            "judged_attempts": len(prior), "eval_run_ids": [e.id for e, _ in prior],
            "workspace_files": len(tools.workspace.files()) if tools is not None else None,
        })
        for number, (prior_eval, prior_experience) in enumerate(prior, start=1):
            eval_run_ids.append(prior_eval.id)
            stdout = ((prior_eval.facts or {}).get("candidate_execution") or {}).get("stdout") or ""
            passed = (prior_eval.oracle_verdict is OracleVerdict.PASS
                      and prior_eval.sandbox_status is ExecutionStatus.SUCCESS)
            if passed:
                category = None
            elif prior_eval.sandbox_status is ExecutionStatus.SUCCESS:
                category = _judged_failure_category(task, stdout)
            else:
                category = classify_execution_status(prior_eval.sandbox_status or ExecutionStatus.FAILED)
            history.add(AttemptRecord(
                attempt_number=number, provider_name=prior_eval.model_id or "inconnu",
                sandbox_level=str((prior_eval.facts or {}).get("isolation_level") or "inconnu"),
                category=category, reason="tentative jugée avant l'interruption (reprise)", strategy=None,
                judge_result=prior_eval.oracle_verdict.value if passed else None,
                started_at=prior_eval.started_at or _utcnow(), duration_s=float(prior_eval.elapsed_s or 0.0),
                reassigned=False, decision=None if passed else RecoveryAction.RETRY,
            ))
            if passed:
                # Le Judge avait DÉJÀ validé avant l'interruption : rien n'est régénéré ni rejugé.
                return _finalize(
                    task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.COMPLETED,
                    final_output=stdout, attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                    eval_run_ids=tuple(eval_run_ids), failure=None, stop_reason=StopReason.SUCCESS.value, emit=emit,
                )
        attempt_number = len(prior)
        retries_used = len(prior)
        if prior and request.retry_adaptation == "failure_facts":
            last_eval, last_experience = prior[-1]
            if last_experience is not None:
                adapted = _adapted_strategy(strategy, last_experience, last_eval, attempt_number)
                if adapted is not None:
                    strategy = adapted
                    generation_prompt = _compose_prompt(prompt, strategy.genome_content,
                                                        plan_closing=plan_closing)
                    generation_temperature = resolve_generation_temperature(strategy.genome_content)
                    emit("strategy.adapted", {"after_attempt": attempt_number, **strategy.event()})

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
            spawned_by=request.authorization, mission_id=request.mission_id,
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
            endpoint=current_candidate.endpoint, prompt=generation_prompt, temperature=generation_temperature,
            timeout_s=generation_timeout_s,
        )
        provider = provider_factory(current_candidate)
        try:
            if tools is None:
                generation = provider.generate(generation_request)
            else:
                generation = _run_episode(
                    tools, request=request, provider=provider, generation_request=generation_request,
                    agent_runtime=agent_runtime, sandbox=sandbox, pool=pool, limits=limits,
                    clone_subject=clone_subject, trace_id=trace_id, attempt_number=attempt_number,
                    cancel_token=cancel_token, deadline=started_monotonic + request.limits.max_wall_clock_s,
                    emit=emit, task=task,
                )
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
        if cancel_token is not None and cancel_token.is_cancel_requested:
            # Annulation demandée PENDANT la génération : rien n'est exécuté ni jugé ensuite.
            agent_runtime.terminate(clone, requested_by=request.authorization, reason="annulation demandée")
            return _finalize(
                task_id=request.task_id, trace_id=trace_id, status=TaskLoopStatus.CANCELLED,
                final_output=None, attempts=tuple(history.records), providers_tried=tuple(providers_tried),
                eval_run_ids=tuple(eval_run_ids), failure=None, stop_reason=StopReason.CANCELLED.value, emit=emit,
            )

        # -- EXECUTE -----------------------------------------------------
        eval_run_id = str(uuid.uuid4())
        if task.effective_kind is TaskKind.PROJECT and tools is not None:
            # Projet : le harnais caché (écrit par APEX) importe les modules d'un INSTANTANÉ de l'espace.
            spec = SandboxSpec(
                mission_id=eval_run_id, clone_id=clone.clone_id, command=("python3", "-I", "-c", task.solution_harness),
                limits=limits, input_files=tools.workspace.snapshot(),
            )
        else:
            command = build_candidate_command(
                solution_harness=task.solution_harness, candidate_code=generation.text,
            )
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
            generation=generation,
        )
        eval_run_ids.append(eval_run.id)
        fitness = compute_fitness(eval_run, limits=limits)
        experience = build_experience(
            eval_run, fitness, strategy_id=f"candidate:{current_candidate.ref.id}",
            generation_number=strategy.generation_number, generation_config=strategy.genome_content or {},
            created_by=request.authorization.value,
            parent_experience_id=strategy.source.id if strategy.source else None,
            parent_generation=strategy.source.generation_number if strategy.source else None,
            mutation_description=strategy.description if strategy.conditioned else None,
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
            category = _judged_failure_category(task, sandbox_result.stdout)
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

        # -- DIAGNOSTIC -> CORRECTION (avant la tentative suivante) -----------
        if request.retry_adaptation == "failure_facts":
            adapted = _adapted_strategy(strategy, experience, eval_run, attempt_number)
            if adapted is None:
                emit("strategy.unchanged", {
                    "after_attempt": attempt_number,
                    "reason": f"catégorie « {experience.failure_category.value} » : aucun fait d'échec exploitable",
                })
            else:
                strategy = adapted
                generation_prompt = _compose_prompt(prompt, strategy.genome_content,
                                                    plan_closing=plan_closing)
                generation_temperature = resolve_generation_temperature(strategy.genome_content)
                emit("strategy.adapted", {"after_attempt": attempt_number, **strategy.event()})


def _judged_attempts(
    request: TaskRequest, *, trace_id: str, experience_repo: ExperienceRepository, eval_run_repo: EvalRunRepository,
) -> list[tuple[EvalRun, Any]]:
    """Tentatives de `trace_id` RÉELLEMENT jugées avant l'interruption (une `EvalRun` persistée = une tentative
    achevée ; une tentative coupée avant son jugement n'a rien laissé et sera refaite), dans l'ordre, chacune
    avec son `Experience` si elle a été écrite."""
    runs = [r for r in eval_run_repo.list_by_experiment_ref_prefix(trace_id, limit=200)
            if r.experiment_ref == trace_id and r.task_id == request.task_id]
    runs.sort(key=lambda r: (r.started_at or _utcnow(), r.id))
    experiences = {e.eval_run_id: e for e in experience_repo.list_by_task(request.task_id, limit=500)
                   if e.experiment_ref == trace_id}
    return [(r, experiences.get(r.id)) for r in runs]


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
    decided = decision.to_dict()
    # La politique ANNONCE un changement de stratégie ; ce que la boucle fait RÉELLEMENT entre deux
    # tentatives est dit à côté (`retry_adaptation`, puis l'événement `strategy.adapted`/`strategy.unchanged`).
    decided["retry_adaptation"] = request.retry_adaptation
    emit("recovery.decided", decided)

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
    created_by: str, experiment_ref: str, eval_run_repo: EvalRunRepository, generation: Any = None,
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
    # Niveau d'isolation RÉELLEMENT appliqué à cette exécution (même clé que `SandboxResult.to_dict`) :
    # `facts` du backend n'en porte que la description.
    facts["isolation_level"] = sandbox_result.isolation_level.value
    if generation is not None:
        # Le code RÉELLEMENT généré, au même format qu'`EvalRunnerService.run_generated_solution`
        # (`facts["model_response"]`, borné) : sans lui, la solution produite par le modèle était
        # irrécupérable et `Experience.solution_fingerprint` restait toujours vide pour le TaskLoop
        # (constat de l'audit du 03/10/2026).
        from apex.evalrunner.service import EvalRunnerService

        facts.update(EvalRunnerService._model_response_facts(generation))
        if isinstance(getattr(generation, "facts", None), dict) and "tool_episode" in generation.facts:
            facts["tool_episode"] = generation.facts["tool_episode"]
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
