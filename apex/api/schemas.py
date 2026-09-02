"""Schémas Pydantic — la forme des réponses HTTP, jamais une nouvelle source
de vérité. Chaque schéma projette un objet du Core (`apex.gate`, `apex.obs`,
`apex.interfaces.registry`, `apex.db.models`, `apex.agents.view`) ; aucun
champ n'est calculé ici.

`DataEnvelope` est la forme commune à TOUTE réponse dont la disponibilité
dépend d'une ressource externe (DB, rapport Gate) : `status` doit toujours
être lu avant `data` (docs/COGNITIVE_ARCHITECTURE.md §12). `/registry` est la
seule route qui n'a jamais besoin d'être autre chose que `"real"` : elle ne
lit que la mémoire du processus Python, sans dépendance externe.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DataStatusLiteral = Literal["real", "simulated", "unknown"]


class DataEnvelope(BaseModel, Generic[T]):
    status: DataStatusLiteral
    data: T | None = None
    detail: str | None = None


class MeasurementOut(BaseModel):
    value: Any
    status: DataStatusLiteral


# -- health ------------------------------------------------------------------


class HealthComponentOut(BaseModel):
    component: str
    status: Literal["ok", "degraded", "failed"]
    detail: str
    version: str | None
    fallback_active: bool
    latency_ms: float | None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded", "failed"]
    components: list[HealthComponentOut]


# -- gate ----------------------------------------------------------------


class GateCheckOut(BaseModel):
    check_id: str
    title: str
    status: str
    message: str


class GateOut(BaseModel):
    gate_status: str
    phase_2_authorized: bool
    machine_label: str | None
    fingerprint: str
    measured_on_target_machine: bool
    checks: list[GateCheckOut]


# -- registry ----------------------------------------------------------------


class AbstractionOut(BaseModel):
    name: str
    purpose: str
    status: str
    phase_defined: int
    phase_implemented: int
    fallback: str
    module: str | None
    notes: str


class RegistryOut(BaseModel):
    summary: dict[str, int]
    abstractions: list[AbstractionOut]


# -- events / audit ------------------------------------------------------


class EventOut(BaseModel):
    id: int
    ts: str
    level: str
    logger: str
    msg: str
    context: dict[str, Any]


class AuditLogOut(BaseModel):
    id: int
    ts: str
    actor: str
    action: str
    target: str | None
    details: dict[str, Any]


# -- agents ----------------------------------------------------------------


class AgentOut(BaseModel):
    clone_id: str
    type: str
    state: str
    mission_id: str | None
    genome_ref: str
    pool_id: str
    isolation_level: MeasurementOut
    created_at: str
    started_at: str | None
    terminated_at: str | None
    elapsed_s: MeasurementOut
    cpu_time_s: MeasurementOut
    ram_mb: MeasurementOut
    gpu: MeasurementOut
    retries_used: int
    max_retries: int
    last_error: str | None
    available_actions: list[str]


# -- missions ----------------------------------------------------------------


class MissionOut(BaseModel):
    id: str
    title: str
    description: str
    specialty: str
    objective: str
    priority: str
    constraints: str | None
    assigned_agent: str | None
    #: Passe « Agents V1 » — référence un `Agent` (`ConfiguredAgentOut`),
    #: DISTINCT d'`assigned_agent` ci-dessus (un clone OS-level réel).
    agent_id: str | None
    state: str
    result: str | None
    error: str | None
    validation_status: str | None
    created_by: str
    created_at: str
    started_at: str | None
    completed_at: str | None


class MissionCreateIn(BaseModel):
    """Corps de `POST /missions`. `priority` accepte les mêmes valeurs que
    `apex.missions.model.MissionPriority` ; une valeur hors catalogue est
    rejetée par Pydantic avant même d'atteindre le service."""

    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    specialty: str
    objective: str = Field(min_length=1)
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    constraints: str | None = None
    assigned_agent: str | None = None
    agent_id: str | None = None


class MissionRejectIn(BaseModel):
    reason: str | None = None


# -- scheduler (étape 11) -----------------------------------------------------


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    created_by: str
    created_at: str


class ProjectCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ConversationOut(BaseModel):
    id: str
    title: str
    project_id: str | None
    created_by: str
    created_at: str
    updated_at: str


class ConversationCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None


class ConversationRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    model_id: str | None
    provider_id: str | None
    status: str
    error: str | None
    created_at: str


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1)
    model_role: str | None = None


class SendMessageOut(BaseModel):
    """Les DEUX messages produits par un tour de Chat — jamais seulement la
    réponse : le message utilisateur porte l'horodatage/l'id que le client
    doit utiliser pour l'afficher sans le reconstruire lui-même."""

    user_message: MessageOut
    assistant_message: MessageOut


# -- agent templates (passe « Première interface utilisable ») ---------------


class AgentTemplateOut(BaseModel):
    id: str
    name: str
    role: str
    model_role: str | None
    tools: list[str]
    description: str
    status: str
    created_by: str
    created_at: str


class AgentTemplateCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str
    description: str = ""
    model_role: str | None = None
    tools: list[str] = Field(default_factory=list)


# -- Agent / AgentExecution / AgentTeam (passe « Agents V1 ») -----------------
#
# `AgentOut` existe DÉJÀ (ligne ~114, projection d'un `Clone` réel —
# `AgentRuntime`) : ces schémas portent le préfixe `Configured`/`AgentEntity`
# pour ne jamais entrer en collision, ni côté nommage ni côté sens — un
# `ConfiguredAgentOut` est une configuration réutilisable, jamais une
# instance de clone OS-level en cours d'exécution.


class ConfiguredAgentOut(BaseModel):
    id: str
    name: str
    description: str
    role: str
    specialty: str | None
    model_role: str | None
    required_capabilities: list[str]
    tools: list[str]
    permissions: list[str]
    instructions: str | None
    enabled: bool
    status: str
    project_id: str | None
    template_id: str | None
    created_by: str
    created_at: str
    updated_at: str


class ConfiguredAgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str
    description: str = ""
    specialty: str | None = None
    model_role: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    instructions: str | None = None
    enabled: bool = True
    project_id: str | None = None
    #: Si fourni, copie la configuration du template (§3 de la directive) —
    #: `role`/`model_role` du corps sont alors ignorés au profit du template.
    template_id: str | None = None


class ConfiguredAgentUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    model_role: str | None = None
    required_capabilities: list[str] | None = None
    tools: list[str] | None = None
    permissions: list[str] | None = None
    instructions: str | None = None
    enabled: bool | None = None


class AgentExecutionOut(BaseModel):
    id: str
    agent_id: str
    mission_id: str
    status: str
    model_id: str | None
    provider_id: str | None
    result: str | None
    error: str | None
    fallback_reason: str | None
    started_at: str
    completed_at: str | None


class RunAgentExecutionIn(BaseModel):
    prompt: str | None = None


class AgentTeamOut(BaseModel):
    id: str
    name: str
    description: str
    agent_ids: list[str]
    created_by: str
    created_at: str


class AgentTeamCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    agent_ids: list[str] = Field(default_factory=list)


# -- model/provider catalog (passe « Model/Provider Catalog ») ---------------


class ProviderInfoOut(BaseModel):
    id: str
    display_name: str
    runtime: str
    base_url: str
    secret_key: str | None
    local: bool
    enabled: bool
    model_ids: list[str]


class CatalogModelOut(BaseModel):
    model_id: str
    display_name: str
    role: str
    family: str
    version: str
    quantization: str
    capability_class: str
    is_local: bool
    is_declared_available: bool
    capabilities: list[str]
    availability: str
    discovery: str
    provider_id: str
    runtime: str | None
    served_model: str | None
    max_context: int | None
    vram_gb: float | None


class SchedulerStatusOut(BaseModel):
    """Projection de `apex.scheduler.capacity.CapacityView` + l'état RÉEL de
    `ExecutionRegistry` de ce processus. `heartbeat` reste `unknown` tant que
    `HeartbeatService` n'est pas réellement démarré (arbitrage humain étape
    11, point B) — jamais une valeur fabriquée du seul fait que le code de
    `HeartbeatService` existe."""

    max_concurrent_missions: int
    running_missions: int
    available_slots: int
    tracked_executions: int
    heartbeat: MeasurementOut
