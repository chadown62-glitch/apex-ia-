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


class MissionRejectIn(BaseModel):
    reason: str | None = None


# -- scheduler (étape 11) -----------------------------------------------------


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
