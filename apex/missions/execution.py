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
"""

from __future__ import annotations

from typing import Any

from apex.agents.model import Clone, CloneState
from apex.agents.runtime import AgentRuntime
from apex.errors import IsolationUnavailable
from apex.missions.model import Mission
from apex.missions.service import MissionService
from apex.policy.model import Subject
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
