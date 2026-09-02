"""Modèles de domaine — `Agent` (instance configurée, réelle, distincte de
`apex.agent_templates.AgentTemplate` — un blueprint — et d'`apex.agents.
model.Clone` — une instance de clone OS-level réellement spawnée,
`AgentRuntime`). Un `Agent` de ce module ne spawn JAMAIS de clone lui-même :
il configure QUEL modèle/quels outils/quelles permissions une exécution
utilisera, exécutée au niveau orchestrateur, HORS sandbox — même niveau de
confiance qu'`apex.missions.execution.generate_mission_response_via_router`,
dont `AgentExecution` réutilise directement les primitives (`apex.routing.
select_model`/`generate_with_fallback`, `apex.missions.service.
MissionService.mark_running/mark_review/mark_failed`) plutôt que de dupliquer
le cycle de vie de Mission.

Dataclasses pures, aucune dépendance DB (même séparation que `apex.missions.
model.Mission` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from apex.agent_templates.model import AgentRole

if TYPE_CHECKING:
    from apex.db.models import AgentExecutionRow, AgentTeamRow, ConfiguredAgentRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Permission(str, Enum):
    """Vocabulaire fermé — jamais accordé automatiquement (§7 de la
    directive « Agents V1 ») : un `Agent` créé sans permission explicite n'a
    AUCUNE de ces capacités, refus par défaut, même discipline que
    `PolicyEngine.DEFAULT_ALLOWLIST`."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXECUTE = "execute"
    NETWORK = "network"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"


class ToolName(str, Enum):
    """Vocabulaire fermé des outils qu'un `Agent` peut se voir déclarer.
    `ToolRegistry`/`ToolProvider` (registre : `PLANNED`, aucun code) —
    déclarer un outil ici n'en fait jamais un outil réellement exécutable ;
    c'est une AUTORISATION préparée pour le jour où `ToolRegistry` existe,
    jamais une exécution simulée. `WEB_RESEARCH` reste structurellement
    déclarable mais n'est JAMAIS considéré utilisable (voir `is_operational`)
    faute d'implémentation réelle — la directive §12 est explicite : ne pas
    simuler l'accès web."""

    FILES = "files"
    TERMINAL = "terminal"
    TESTS = "tests"
    GIT_READ = "git-read"
    SANDBOX = "sandbox"
    MEMORY = "memory"
    STATIC_ANALYSIS = "static-analysis"
    WEB_RESEARCH = "web-research"

    @property
    def is_operational(self) -> bool:
        """`False` pour tout outil sans exécuteur réel derrière
        (`ToolRegistry` `PLANNED`) — aujourd'hui, aucun ne l'est : ce champ
        existe pour que l'UI/l'API ne prétendent jamais qu'un outil déclaré
        est utilisable avant qu'un vrai exécuteur existe."""
        return False


class AgentStatus(str, Enum):
    """Vocabulaire fermé, JAMAIS stocké — toujours DÉRIVÉ (`derive_agent_
    status`) de `Agent.enabled` + de la dernière `AgentExecution` réelle,
    même discipline que `GenomeVersionRepository.get_current` (jamais un
    pointeur mutable séparé qui pourrait diverger). `WAITING` reste dans le
    vocabulaire fermé (prêt pour une future attente réelle — dépendance,
    outil externe) mais n'est JAMAIS dérivé aujourd'hui — aucune source ne
    le produit encore."""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    DISABLED = "disabled"


class AgentExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FallbackReason(str, Enum):
    """Vocabulaire fermé, demandé explicitement (directive §24). Seuls
    `SUCCESS`/`TIMEOUT`/`CAPABILITY_MISSING`/`PROVIDER_ERROR` sont
    RÉELLEMENT dérivables aujourd'hui — voir `classify_outcome` : `apex.
    errors.ModelProviderError` ne porte aucun code HTTP structuré (jamais un
    corps de réponse conservé, §72), donc `QUOTA_EXCEEDED`/`RATE_LIMITED`/
    `OFFLINE`/`AUTH_ERROR`/`CONTEXT_TOO_LONG` restent déclarés dans le
    vocabulaire fermé (prêts) mais ne sont JAMAIS produits par le
    classificateur — les distinguer exigerait que les providers exposent un
    code d'erreur structuré, pas fait à ce jour (deviner depuis un message
    texte serait exactement l'erreur que ce dépôt interdit ailleurs)."""

    SUCCESS = "success"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    AUTH_ERROR = "auth_error"
    CAPABILITY_MISSING = "capability_missing"
    CONTEXT_TOO_LONG = "context_too_long"
    PROVIDER_ERROR = "provider_error"


@dataclass
class Agent:
    """Une entité réutilisable, persistée, assignable à plusieurs Missions.
    `required_capabilities` référence `apex.routing.model.Capability`
    (vocabulaire déjà fermé, jamais dupliqué) — validé à la création.
    `model_role` filtre `apex.models.registry.resolve_candidates` par rôle
    de `config.yaml`, une convention déjà établie (`ConversationService.
    send_message`), jamais une nouvelle. Le Router (`apex.routing.
    select_model`) reste seul responsable du choix final."""

    id: str
    name: str
    description: str
    role: AgentRole
    specialty: str | None
    model_role: str | None
    required_capabilities: tuple[str, ...]
    tools: tuple[ToolName, ...]
    permissions: tuple[Permission, ...]
    instructions: str | None
    enabled: bool
    project_id: str | None
    template_id: str | None
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def has_tool(self, tool: ToolName) -> bool:
        return tool in self.tools

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions

    @classmethod
    def new(
        cls,
        *,
        name: str,
        role: AgentRole,
        created_by: str,
        description: str = "",
        specialty: str | None = None,
        model_role: str | None = None,
        required_capabilities: tuple[str, ...] = (),
        tools: tuple[ToolName, ...] = (),
        permissions: tuple[Permission, ...] = (),
        instructions: str | None = None,
        enabled: bool = True,
        project_id: str | None = None,
        template_id: str | None = None,
    ) -> Agent:
        now = _utcnow()
        return cls(
            id=str(uuid.uuid4()), name=name, description=description, role=role,
            specialty=specialty, model_role=model_role, required_capabilities=required_capabilities,
            tools=tools, permissions=permissions, instructions=instructions, enabled=enabled,
            project_id=project_id, template_id=template_id, created_by=created_by,
            created_at=now, updated_at=now,
        )

    @classmethod
    def from_row(cls, row: "ConfiguredAgentRow") -> Agent:
        return cls(
            id=row.id, name=row.name, description=row.description, role=AgentRole(row.role),
            specialty=row.specialty, model_role=row.model_role,
            required_capabilities=tuple(row.required_capabilities or ()),
            tools=tuple(ToolName(t) for t in (row.tools or ())),
            permissions=tuple(Permission(p) for p in (row.permissions or ())),
            instructions=row.instructions, enabled=row.enabled, project_id=row.project_id,
            template_id=row.template_id, created_by=row.created_by,
            created_at=row.created_at, updated_at=row.updated_at,
        )


@dataclass
class AgentExecution:
    """Une exécution réelle d'un `Agent` pour une `Mission`. `model_id`/
    `provider_id` restent `None` tant qu'aucun candidat n'a réellement été
    servi (jamais une valeur devinée) ; `error` porte TOUJOURS le détail
    complet des tentatives, jamais seulement la dernière (même discipline
    que `generate_mission_response_via_router`)."""

    id: str
    agent_id: str
    mission_id: str
    status: AgentExecutionStatus
    model_id: str | None
    provider_id: str | None
    result: str | None
    error: str | None
    fallback_reason: FallbackReason | None
    started_at: datetime
    completed_at: datetime | None
    #: Passe « Adaptive Model Router V1 » — mêmes noms qu'`apex.models.
    #: model.GenerationResult` (`elapsed_s`/`tokens_prompt`/
    #: `tokens_completion`). `None` tant qu'aucune génération n'a
    #: réellement abouti, ou que le backend n'a pas fourni le comptage de
    #: tokens — jamais une valeur devinée à la place d'une mesure absente.
    elapsed_s: float | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None

    @classmethod
    def from_row(cls, row: "AgentExecutionRow") -> AgentExecution:
        return cls(
            id=row.id, agent_id=row.agent_id, mission_id=row.mission_id,
            status=AgentExecutionStatus(row.status), model_id=row.model_id, provider_id=row.provider_id,
            result=row.result, error=row.error,
            fallback_reason=FallbackReason(row.fallback_reason) if row.fallback_reason else None,
            started_at=row.started_at, completed_at=row.completed_at,
            elapsed_s=row.elapsed_s, tokens_prompt=row.tokens_prompt, tokens_completion=row.tokens_completion,
        )


def derive_agent_status(agent: Agent, latest_execution: AgentExecution | None) -> AgentStatus:
    """Fonction PURE — jamais un état stocké. Voir la docstring
    d'`AgentStatus` pour pourquoi `WAITING` n'est jamais retourné ici."""
    if not agent.enabled:
        return AgentStatus.DISABLED
    if latest_execution is None:
        return AgentStatus.IDLE
    return {
        AgentExecutionStatus.QUEUED: AgentStatus.QUEUED,
        AgentExecutionStatus.RUNNING: AgentStatus.RUNNING,
        AgentExecutionStatus.COMPLETED: AgentStatus.COMPLETED,
        AgentExecutionStatus.FAILED: AgentStatus.FAILED,
    }[latest_execution.status]


@dataclass
class AgentTeam:
    """Structure V1 délibérément minimale (directive §13) — nom, membres,
    rôles déjà portés par chaque `Agent`. Aucune communication autonome
    entre agents : une Team ne fait qu'énumérer qui en fait partie."""

    id: str
    name: str
    description: str
    agent_ids: tuple[str, ...]
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(cls, *, name: str, description: str, agent_ids: tuple[str, ...], created_by: str) -> AgentTeam:
        return cls(
            id=str(uuid.uuid4()), name=name, description=description,
            agent_ids=agent_ids, created_by=created_by,
        )

    @classmethod
    def from_row(cls, row: "AgentTeamRow") -> AgentTeam:
        return cls(
            id=row.id, name=row.name, description=row.description,
            agent_ids=tuple(row.agent_ids or ()), created_by=row.created_by, created_at=row.created_at,
        )
