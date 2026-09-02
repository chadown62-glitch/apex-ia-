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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from apex.agents.model import Clone, CloneState
from apex.agents.runtime import AgentRuntime
from apex.config.schema import ExternalPolicy, ModelEndpoint
from apex.errors import IsolationUnavailable, ModelProviderError, TimeoutExceeded
from apex.missions.model import Mission
from apex.missions.service import MissionService
from apex.models.model import GenerationRequest
from apex.models.provider import ModelProvider, get_default_provider as get_default_model_provider
from apex.models.registry import ModelCandidate
from apex.policy.model import Subject
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
) -> Mission:
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

    resolved_sandbox = sandbox or get_default_provider()
    resolved_limits = limits or _default_limits(session_factory)
    resolved_command = command
    if resolved_command is None:
        from apex.missions.workloads import research_diagnostic_command

        resolved_command = research_diagnostic_command()

    spec = SandboxSpec(
        mission_id=mission.id, clone_id=clone.clone_id,
        command=resolved_command, limits=resolved_limits,
    )

    # 2. AGENT RUNTIME -> SANDBOX. `execute_in_sandbox` vérifie elle-même
    # Action.EXECUTE_IN_SANDBOX (CORE seul) et l'état READY du clone.
    try:
        result = agent_runtime.execute_in_sandbox(
            clone, resolved_sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
        )
    except IsolationUnavailable as exc:
        return mission_service.mark_failed(mission_id, error=f"sandbox indisponible : {exc}")

    # 3. MISSION -> RESULT -> REVIEW (ou FAILED). La décision COMPLETED/rejetée
    # reste HUMAN (validate/reject, apex.missions.service) — jamais automatique.
    if result.status is ExecutionStatus.SUCCESS:
        return mission_service.mark_review(mission_id, result=result.stdout)
    if result.status is ExecutionStatus.CANCELLED:
        # Étape 8 : la transition RUNNING -> CANCELLED est décidée UNE seule
        # fois, par `apex.scheduler.service.SchedulerService.cancel_mission`
        # (qui a demandé cette annulation) — jamais ici, pour éviter que
        # deux chemins ne se disputent la finalisation de l'état de la
        # mission. On se contente de renvoyer l'état réellement persisté.
        return mission_service.get(mission_id) or mission
    return mission_service.mark_failed(
        mission_id, error=result.error_detail or result.status.value,
    )


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
