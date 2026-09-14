"""Orchestrateur qui relie Mission Engine, AgentRuntime et Sandbox — la
chaîne obligatoire de l'étape 7, dans cet ordre exact, sans raccourci :

    CORE -> POLICY -> AGENT RUNTIME -> SANDBOX -> MISSION -> RESULT
    -> REVIEW -> HUMAN -> PROMOTION éventuelle

Ni `apex.missions.service` ni `apex.agents.runtime` ne se connaissent l'un
l'autre : ce module est le SEUL point qui les compose, exactement comme
`apex.missions.service.create()` compose déjà `apex.specialties.registry`
et `apex.db.repositories.AgentRepository` sans que ces modules ne
s'importent mutuellement. Ajouter un backend Sandbox plus tard ne change
rien ici — seul `apex.sandbox.provider.get_default_provider()` choisirait
différemment.

**Validation de pré-vol AVANT toute transition de mission** (mission
inexistante, aucun agent assigné, agent introuvable, clone pas READY) : ces
erreurs sont levées directement, la mission reste QUEUED — jamais laissée
bloquée en RUNNING pour une raison qui n'a rien à voir avec l'exécution
elle-même. Une fois `mark_running` passé, la mission DOIT atteindre un état
terminal-ish (REVIEW ou FAILED) : `IsolationUnavailable` est la seule
exception rattrapée après ce point, traduite en échec de mission — jamais
un contournement silencieux, mais jamais non plus une mission orpheline en
RUNNING indéfiniment.

**Second chemin d'exécution — `generate_mission_response`** : une Mission
peut aussi être exécutée par un `ModelProvider` réel plutôt qu'une commande
sandboxée, HORS sandbox (voir sa docstring pour le raisonnement complet —
étape intermédiaire reconnue, PAS un contournement, en attendant un vrai
contrôle d'egress réseau sandboxé). Les deux chemins partagent
`MissionService` et convergent vers REVIEW/FAILED de façon identique, mais
ne se composent jamais entre eux : une Mission suit soit `launch_mission`
(clone + sandbox), soit `generate_mission_response` (modèle), jamais un
mélange implicite des deux dans la même fonction.

**Troisième chemin — `generate_mission_response_via_router`** : comme
`generate_mission_response`, mais le `ModelEndpoint` n'est plus fourni par
l'appelant — `apex.routing.select_model` le choisit parmi des
`ModelCandidate` déjà réels (`apex.models.registry.resolve_candidates`),
puis `apex.routing.generate_with_fallback` essaie les candidats classés
dans l'ordre jusqu'au premier succès. `generate_mission_response` n'est PAS
modifiée par ce troisième chemin — un appelant qui fournit déjà son
`ModelEndpoint` continue de l'utiliser sans aucun changement de
comportement.

**N+26 — recovery/retry/reassignment, STRICTEMENT opt-in.** `launch_mission`
accepte désormais `pool`/`alternate_pools`/`max_attempts`, tous optionnels.
`pool is None` (la valeur par défaut) préserve EXACTEMENT le comportement
historique — une seule tentative, `IsolationUnavailable`/tout échec ->
`mark_failed` immédiat, AUCUN appel à `apex.recovery` — vérifié par
l'intégralité de la suite de tests existante, inchangée. Fournir `pool`
active la boucle bornée décrite par `apex.recovery.policy.decide` : chaque
nouvelle tentative (`RETRY`/`REASSIGN`) spawn un NOUVEAU `Clone`
(`AgentRuntime.spawn`), jamais une reprise du clone en échec — cohérent
avec l'invariant déjà documenté par `apex.scheduler.recovery.
RecoveryService`/`AgentRuntime.recover_orphan` (« jamais de reprise, une
tentative authentiquement nouvelle »). `CANCELLED` continue de court-
circuiter la boucle exactement comme avant : `apex.scheduler.service.
SchedulerService.cancel_mission` reste seul juge de cette transition.

`classify_sandbox_outcome` (`apex.recovery.classify`) ne produit, depuis ce
chemin, que `SANDBOX_FAILURE`/`TIMEOUT`/`CANCELLED`/`RESOURCE_EXHAUSTED`/
`EXECUTION_FAILURE` — `launch_mission` n'exécute ni Judge ni appel
`ModelProvider` direct, donc `INCORRECT_SOLUTION`/`PROVIDER_FAILURE`/
`PROVIDER_UNAVAILABLE`/`INVALID_CANDIDATE` ne sont JAMAIS atteintes ici
aujourd'hui (elles restent câblées et testées au niveau politique — voir
`tests/test_recovery_policy.py` — pour un futur appelant qui les produirait
réellement). `PROVIDER_FAILURE`/`PROVIDER_UNAVAILABLE` ont déjà un
mécanisme de reassignment RÉEL et INDÉPENDANT :
`generate_mission_response_via_router`/`apex.routing.generate_with_fallback`
— cette boucle ne le duplique jamais.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from apex.agents.model import Clone, CloneState
from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool, ExternalPolicy, ModelEndpoint
from apex.errors import IsolationUnavailable, ModelProviderError, TimeoutExceeded
from apex.missions.model import Mission
from apex.missions.service import MissionService
from apex.models.model import GenerationRequest
from apex.models.provider import ModelProvider
from apex.models.provider import get_default_provider as get_default_model_provider
from apex.models.registry import ModelCandidate
from apex.policy.model import Subject
from apex.recovery import AttemptHistory, AttemptRecord, RecoveryAction, classify_sandbox_outcome, decide
from apex.routing.generation import generate_with_fallback
from apex.routing.model import RoutingRequest
from apex.routing.router import select_model
from apex.sandbox import (
    CancellationToken,
    ExecutionStatus,
    ResourceLimits,
    SandboxProvider,
    SandboxSpec,
    get_default_provider,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_limits(session_factory: Any) -> ResourceLimits:
    from apex.config.loader import load_config

    config = load_config()
    return ResourceLimits.from_config(config.sandbox)


def _get_clone(session_factory: Any, clone_id: str) -> Clone | None:
    from apex.db.engine import session_scope
    from apex.db.repositories import AgentRepository

    with session_scope(session_factory) as session:
        row = AgentRepository(session).get(clone_id)
        return Clone.from_row(row) if row is not None else None


def _record_mission_event(
    session_factory: Any, mission: Mission, kind: str, details: dict[str, Any],
) -> None:
    """Même patron que `MissionService._record_event`/`AgentRuntime.
    _record_event` — jamais un troisième mécanisme de journalisation."""
    from apex.db.engine import session_scope
    from apex.db.repositories import EventRepository

    with session_scope(session_factory) as session:
        EventRepository(session).add(
            ts=_utcnow(), level="INFO", logger="apex.missions.execution",
            msg=kind, context={"mission_id": mission.id, "kind": kind, **details},
        )


def _rotate_pool(pools: tuple[ComputePool, ...], current: ComputePool) -> ComputePool:
    """Alterne vers le pool RÉEL suivant — jamais un choix arbitraire.
    `pools` a toujours au moins `current` dedans (voir son unique appelant) ;
    un seul pool disponible retourne `current` inchangé (aucune alternative
    réelle, la politique ne devrait alors jamais avoir décidé REASSIGN)."""
    if len(pools) <= 1:
        return current
    ids = [p.pool_id for p in pools]
    idx = ids.index(current.pool_id)
    return pools[(idx + 1) % len(pools)]


def launch_mission(
    mission_id: str,
    *,
    session_factory: Any,
    mission_service: MissionService,
    agent_runtime: AgentRuntime,
    sandbox: SandboxProvider | None = None,
    command: tuple[str, ...] | None = None,
    limits: ResourceLimits | None = None,
    requested_by: Subject = Subject.CORE,
    cancel_token: CancellationToken | None = None,
    pool: ComputePool | None = None,
    alternate_pools: tuple[ComputePool, ...] = (),
    max_attempts: int | None = None,
) -> Mission:
    """`pool`/`alternate_pools`/`max_attempts` : voir la docstring du module,
    §N+26 — tous optionnels, `pool is None` préserve le comportement
    historique à une seule tentative sans AUCUN changement observable."""
    mission = mission_service.get(mission_id)
    if mission is None:
        raise LookupError(f"mission « {mission_id} » introuvable")
    if mission.assigned_agent is None:
        raise ValueError(
            f"mission « {mission_id} » n'a pas d'agent assigné — impossible de la lancer"
        )

    clone = _get_clone(session_factory, mission.assigned_agent)
    if clone is None:
        raise ValueError(f"agent « {mission.assigned_agent} » introuvable")
    if clone.state is not CloneState.READY:
        raise ValueError(
            f"agent « {clone.clone_id} » n'est pas READY (état actuel : {clone.state.value}) "
            "— impossible de lancer la mission"
        )

    # 1. CORE -> POLICY -> MISSION : QUEUED -> RUNNING. `mark_running` vérifie
    # elle-même Action.LAUNCH_MISSION (CORE seul) et lève InvalidMissionTransition
    # si la mission n'est pas QUEUED — aucune règle de permission dupliquée ici.
    mission = mission_service.mark_running(mission_id, requested_by=requested_by)

    # Résolution INCHANGÉE depuis avant N+26 (même fonction, même provider
    # retourné — `get_default_provider()` délègue déjà à
    # `select_sandbox_provider().provider`, voir apex.sandbox.provider) :
    # aucune différence observable pour un appelant qui ne fournit pas `pool`.
    resolved_sandbox = sandbox or get_default_provider()
    resolved_limits = limits or _default_limits(session_factory)
    resolved_command = command
    if resolved_command is None:
        from apex.missions.workloads import research_diagnostic_command

        resolved_command = research_diagnostic_command()

    if pool is None:
        # Comportement historique, INCHANGÉ — aucun appel à apex.recovery.
        spec = SandboxSpec(
            mission_id=mission.id, clone_id=clone.clone_id,
            command=resolved_command, limits=resolved_limits,
        )
        try:
            result = agent_runtime.execute_in_sandbox(
                clone, resolved_sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
            )
        except IsolationUnavailable as exc:
            return mission_service.mark_failed(mission_id, error=f"sandbox indisponible : {exc}")
        return _finalize(mission_service, mission_id, mission, result)

    # -- N+26 : boucle de recovery bornée, opt-in via `pool` -----------------
    from apex.config.loader import load_config

    recovery_cfg = load_config().recovery
    if max_attempts is not None:
        recovery_cfg = replace(recovery_cfg, max_attempts=max_attempts)

    if sandbox is None:
        # N+26 : câble enfin `SandboxSelection.reason` (N+25 §6, jamais
        # affiché nulle part jusqu'ici) dans la trace observable de la
        # mission — seulement sur ce chemin (opt-in via `pool`), jamais
        # ajouté au comportement historique. Deuxième sonde volontairement
        # bon marché : `detect()` est mis en cache par l'instance déjà
        # construite par `get_default_provider()` ci-dessus.
        from apex.sandbox.provider import select_sandbox_provider

        selection = select_sandbox_provider()
        _record_mission_event(
            session_factory, mission, "provider.selected",
            {"provider_name": selection.provider_name, "reason": selection.reason},
        )

    available_pools = (pool, *alternate_pools)
    current_pool = pool
    current_clone = clone
    history = AttemptHistory()
    attempt_number = 0

    while True:
        attempt_number += 1
        spec = SandboxSpec(
            mission_id=mission.id, clone_id=current_clone.clone_id,
            command=resolved_command, limits=resolved_limits,
        )
        _record_mission_event(
            session_factory, mission, "execution.started",
            {
                "attempt_number": attempt_number, "pool_id": current_pool.pool_id,
                "clone_id": current_clone.clone_id,
            },
        )
        started = time.perf_counter()
        try:
            result = agent_runtime.execute_in_sandbox(
                current_clone, resolved_sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
            )
        except IsolationUnavailable as exc:
            duration_s = time.perf_counter() - started
            category = classify_sandbox_outcome(isolation_unavailable=exc)
            failure_text = str(exc)
            sandbox_level = resolved_sandbox.detect().level.value
        else:
            duration_s = time.perf_counter() - started
            if result.status is ExecutionStatus.SUCCESS:
                history.add(AttemptRecord(
                    attempt_number=attempt_number, provider_name=type(resolved_sandbox).__name__,
                    sandbox_level=result.isolation_level.value, category=None, reason="succès",
                    strategy=None, judge_result=None, started_at=_utcnow(), duration_s=duration_s,
                    reassigned=current_pool is not pool, decision=None,
                ))
                _record_mission_event(
                    session_factory, mission, "execution.completed",
                    {"attempt_number": attempt_number, "status": result.status.value},
                )
                return mission_service.mark_review(mission_id, result=result.stdout)
            if result.status is ExecutionStatus.CANCELLED:
                # Inchangé — voir la docstring du module : jamais interféré
                # par la boucle de recovery.
                return mission_service.get(mission_id) or mission
            category = classify_sandbox_outcome(result=result)
            failure_text = result.error_detail or result.status.value
            sandbox_level = result.isolation_level.value

        decision = decide(
            category, attempt_number=attempt_number, policy=recovery_cfg,
            has_alternate_target=len(available_pools) > 1, previous_failure=failure_text,
        )
        history.add(AttemptRecord(
            attempt_number=attempt_number, provider_name=type(resolved_sandbox).__name__,
            sandbox_level=sandbox_level, category=category, reason=decision.reason,
            strategy=decision.strategy_change, judge_result=None, started_at=_utcnow(),
            duration_s=duration_s, reassigned=False, decision=decision.action,
        ))
        _record_mission_event(session_factory, mission, "recovery.decided", decision.to_dict())

        if decision.action is RecoveryAction.RETRY:
            _record_mission_event(
                session_factory, mission, "retry.started", {"next_attempt_number": attempt_number + 1},
            )
            current_clone = agent_runtime.spawn(
                current_clone.type, pool=current_pool, genome_ref=current_clone.genome_ref,
                spawned_by=requested_by, mission_id=mission.id,
            )
            continue
        if decision.action is RecoveryAction.REASSIGN:
            next_pool = _rotate_pool(available_pools, current_pool)
            _record_mission_event(
                session_factory, mission, "reassignment.decided",
                {"from_pool": current_pool.pool_id, "to_pool": next_pool.pool_id},
            )
            current_pool = next_pool
            current_clone = agent_runtime.spawn(
                current_clone.type, pool=current_pool, genome_ref=current_clone.genome_ref,
                spawned_by=requested_by, mission_id=mission.id,
            )
            continue
        if decision.action is RecoveryAction.ESCALATE:
            _record_mission_event(session_factory, mission, "recovery.escalated", decision.to_dict())
            return mission_service.mark_failed(mission_id, error=f"ESCALATE: {failure_text}")
        # RecoveryAction.BLOCK
        return mission_service.mark_failed(mission_id, error=failure_text)


def _finalize(mission_service: MissionService, mission_id: str, mission: Mission, result: Any) -> Mission:
    """Chemin historique (`pool is None`) — extrait tel quel, comportement
    strictement identique à avant N+26."""
    if result.status is ExecutionStatus.SUCCESS:
        return mission_service.mark_review(mission_id, result=result.stdout)
    if result.status is ExecutionStatus.CANCELLED:
        return mission_service.get(mission_id) or mission
    return mission_service.mark_failed(mission_id, error=result.error_detail or result.status.value)


def generate_mission_response(
    mission_id: str,
    *,
    mission_service: MissionService,
    endpoint: ModelEndpoint,
    model_provider: ModelProvider | None = None,
    prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float = 60.0,
    requested_by: Subject = Subject.CORE,
) -> Mission:
    """DEUXIÈME chemin d'exécution d'une Mission — génération d'une réponse
    par un modèle réel (`ModelProvider`), HORS sandbox. Étape intermédiaire
    reconnue et documentée comme telle : la sandbox namespaced actuelle ne
    supporte que `network="none"` (`apex.sandbox.linux_namespace`, réseau
    « proxy » `PLANNED` — voir le registre, entrée `ModelProvider`) — un
    clone sandboxé ne peut donc PAS appeler un `ModelProvider` réseau
    aujourd'hui. Cet appel a lieu ici, au niveau ORCHESTRATEUR (le même
    niveau de confiance que le CORE qui appelle déjà
    `AgentRuntime.execute_in_sandbox` dans `launch_mission`), jamais depuis
    l'intérieur d'un clone — ce N'EST PAS un contournement de la sandbox,
    c'est un chemin qui ne la traverse simplement pas encore.

    Même patron de gouvernance que `launch_mission` : `mark_running` exige
    déjà `Action.LAUNCH_MISSION` (CORE seul, aucune règle dupliquée ici) ;
    le résultat va dans `Mission.result` (champ déjà existant, aucune
    migration) exactement comme `result.stdout` pour l'exécution sandboxée
    — les deux chemins convergent vers la même relecture humaine (REVIEW).
    Aucune promotion, aucune écriture Genome/Skill : générer une réponse
    n'est jamais, en soi, une preuve de compétence (voir `apex.skills.
    service.SkillRegistryService.propose_from_evidence`, qui exige un
    `EvalRun` jugé indépendamment — jamais un texte généré cru sur parole).
    """
    mission = mission_service.get(mission_id)
    if mission is None:
        raise LookupError(f"mission « {mission_id} » introuvable")

    mission = mission_service.mark_running(mission_id, requested_by=requested_by)

    resolved_provider = model_provider or get_default_model_provider(endpoint)
    resolved_prompt = prompt if prompt is not None else mission.objective

    request = GenerationRequest(
        endpoint=endpoint, prompt=resolved_prompt, max_tokens=max_tokens,
        temperature=temperature, timeout_s=timeout_s,
    )

    try:
        result = resolved_provider.generate(request)
    except (ModelProviderError, TimeoutExceeded) as exc:
        # Même discipline que `OllamaProvider` (§72) : `str(exc)` ne porte
        # jamais le prompt, seulement l'URL de l'endpoint et un résumé
        # borné — sûr à stocker tel quel dans `Mission.error`.
        return mission_service.mark_failed(mission_id, error=str(exc))

    return mission_service.mark_review(mission_id, result=result.text)


def generate_mission_response_via_router(
    mission_id: str,
    *,
    mission_service: MissionService,
    candidates: Sequence[ModelCandidate],
    routing_request: RoutingRequest | None = None,
    external_policy: ExternalPolicy | None = None,
    prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float = 60.0,
    requested_by: Subject = Subject.CORE,
    provider_factory: Callable[[ModelCandidate], ModelProvider] | None = None,
) -> Mission:
    """TROISIÈME chemin d'exécution d'une Mission — comme
    `generate_mission_response`, mais le `ModelEndpoint` est choisi par
    `apex.routing.select_model` parmi `candidates` (typiquement
    `apex.models.registry.resolve_candidates(config)`), puis
    `apex.routing.generate_with_fallback` essaie les candidats classés dans
    l'ordre jusqu'au premier succès.

    Même gouvernance que `generate_mission_response` (HUMAIN par défaut via
    `mark_running`/`Action.LAUNCH_MISSION`, HORS sandbox, aucune promotion).
    `generate_mission_response` reste intacte et continue de fonctionner
    exactement comme avant — ce chemin est additif, jamais un remplacement.

    Un échec total (aucun candidat sélectionné, ou tous les candidats
    essayés ont échoué) ne masque JAMAIS le détail des tentatives
    individuelles : `Mission.error` les porte toutes, jamais seulement la
    dernière.
    """
    mission = mission_service.get(mission_id)
    if mission is None:
        raise LookupError(f"mission « {mission_id} » introuvable")

    mission = mission_service.mark_running(mission_id, requested_by=requested_by)

    decision = select_model(candidates, routing_request or RoutingRequest(), external_policy=external_policy)
    if decision.selected is None:
        return mission_service.mark_failed(
            mission_id, error=f"aucun modèle disponible pour cette Mission : {decision.reason}",
        )

    ranked = [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0]
    resolved_prompt = prompt if prompt is not None else mission.objective

    fallback_kwargs: dict[str, Any] = dict(
        prompt=resolved_prompt, max_tokens=max_tokens, temperature=temperature, timeout_s=timeout_s,
    )
    if provider_factory is not None:
        fallback_kwargs["provider_factory"] = provider_factory
    fallback_result = generate_with_fallback(ranked, **fallback_kwargs)

    if fallback_result.result is None:
        detail = "; ".join(f"{a.candidate.ref.id} : {a.error}" for a in fallback_result.attempts)
        return mission_service.mark_failed(
            mission_id, error=f"tous les candidats ont échoué ({len(fallback_result.attempts)}) — {detail}",
        )

    return mission_service.mark_review(mission_id, result=fallback_result.result.text)
