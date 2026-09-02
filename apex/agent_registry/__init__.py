"""`apex.agent_registry` — Agents V1 : une entité `Agent` réelle, réutilisable,
assignable à des Missions et réellement exécutable via le Model Router déjà
réel (`apex.routing`), distincte d'`apex.agent_templates.AgentTemplate` (un
blueprint, jamais exécutable) et d'`apex.agents.model.Clone` (une instance de
clone OS-level réellement spawnée, `AgentRuntime`).

`AgentRegistryService.run_execution` ne duplique NI `MissionEngine` NI le
Model Router : elle compose les mêmes primitives déjà réelles (`apex.routing.
select_model`/`generate_with_fallback`, `apex.missions.service.
MissionService.mark_running/mark_review/mark_failed`) — une troisième
composition après `generate_mission_response`/`generate_mission_response_
via_router`, jamais une architecture parallèle.
"""

from apex.agent_registry.model import (
    Agent,
    AgentExecution,
    AgentExecutionStatus,
    AgentStatus,
    AgentTeam,
    FallbackReason,
    Permission,
    ToolName,
    derive_agent_status,
)
from apex.agent_registry.service import AgentRegistryService

__all__ = [
    "Agent",
    "AgentExecution",
    "AgentExecutionStatus",
    "AgentStatus",
    "AgentTeam",
    "FallbackReason",
    "Permission",
    "ToolName",
    "derive_agent_status",
    "AgentRegistryService",
]
