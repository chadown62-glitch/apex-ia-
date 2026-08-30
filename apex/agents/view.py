"""Vue d'un clone pour une future API/interface — jamais de donnée inventée.

`DataStatus`/`Measurement` viennent de `apex.obs.status` (déplacés depuis ce
module, où ils étaient nés) — voir ce module pour la justification du
vocabulaire REAL/SIMULATED/UNKNOWN et sa coexistence avec `GateStatus` et
`apex.obs.health.Status`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apex.agents.model import Clone
from apex.obs.status import DataStatus, Measurement
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject

if TYPE_CHECKING:
    # Import différé — même raison que apex/db/event_sink.py : ce module est
    # importé transitivement par apex.agents.runtime (registre :
    # module="apex.agents.runtime"), qui doit rester importable sans l'extra
    # `db` installé. `EventRow` n'est utilisé qu'en annotation de type.
    from apex.db.models import EventRow


#: Actions dont la disponibilité pour LE VIEWER (pas le clone) dépend de son
#: niveau d'autorisation — répond directement à la demande d'interface
#: « actions disponibles selon mon niveau d'autorisation ».
_VIEWER_ACTIONS: tuple[Action, ...] = (Action.TERMINATE_CLONE, Action.PROMOTE_MEMORY_TO_SHARED)


def available_actions(viewer: Subject, policy: PolicyEngine) -> list[str]:
    """Jamais une liste statique : recalculée à chaque appel contre PolicyEngine."""
    return [a.value for a in _VIEWER_ACTIONS if policy.check(viewer, a).allowed]


@dataclass(frozen=True)
class AgentView:
    clone_id: str
    type: str
    state: str
    mission_id: str | None
    genome_ref: str
    pool_id: str
    isolation_level: Measurement
    created_at: str
    started_at: str | None
    terminated_at: str | None
    elapsed_s: Measurement
    cpu_time_s: Measurement
    ram_mb: Measurement
    gpu: Measurement
    retries_used: int
    max_retries: int
    last_error: str | None
    available_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clone_id": self.clone_id,
            "type": self.type,
            "state": self.state,
            "mission_id": self.mission_id,
            "genome_ref": self.genome_ref,
            "pool_id": self.pool_id,
            "isolation_level": self.isolation_level.to_dict(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "terminated_at": self.terminated_at,
            "elapsed_s": self.elapsed_s.to_dict(),
            "cpu_time_s": self.cpu_time_s.to_dict(),
            "ram_mb": self.ram_mb.to_dict(),
            "gpu": self.gpu.to_dict(),
            "retries_used": self.retries_used,
            "max_retries": self.max_retries,
            "last_error": self.last_error,
            "available_actions": self.available_actions,
        }


def to_view(
    clone: Clone,
    *,
    viewer: Subject,
    policy: PolicyEngine,
    elapsed_s: float | None = None,
    cpu_time_s: float | None = None,
) -> AgentView:
    """Construit la vue. `elapsed_s`/`cpu_time_s` viennent d'un `RunResult`
    réel si disponible — jamais recalculés ou estimés ici."""
    elapsed = (
        Measurement(round(elapsed_s, 3), DataStatus.REAL)
        if elapsed_s is not None
        else Measurement(None, DataStatus.UNKNOWN)
    )
    cpu = (
        Measurement(round(cpu_time_s, 3), DataStatus.REAL)
        if cpu_time_s is not None
        else Measurement(None, DataStatus.UNKNOWN)
    )
    isolation_status = DataStatus.UNKNOWN if clone.isolation_level == "unknown" else DataStatus.REAL
    return AgentView(
        clone_id=clone.clone_id,
        type=clone.type.value,
        state=clone.state.value,
        mission_id=clone.mission_id,
        genome_ref=clone.genome_ref,
        pool_id=clone.pool_id,
        isolation_level=Measurement(clone.isolation_level, isolation_status),
        created_at=clone.created_at.isoformat(),
        started_at=clone.started_at.isoformat() if clone.started_at else None,
        terminated_at=clone.terminated_at.isoformat() if clone.terminated_at else None,
        elapsed_s=elapsed,
        cpu_time_s=cpu,
        # RAM/GPU : jamais mesurées sans SandboxProvider (PLANNED, phase 3).
        # UNKNOWN, jamais 0 — un 0 laisserait croire à une mesure réelle nulle.
        ram_mb=Measurement(None, DataStatus.UNKNOWN),
        gpu=Measurement(None, DataStatus.UNKNOWN),
        retries_used=clone.retries_used,
        max_retries=clone.max_retries,
        last_error=clone.last_error,
        available_actions=available_actions(viewer, policy),
    )


def clone_history(events: list[EventRow], clone_id: str) -> list[dict[str, Any]]:
    """Timeline d'événements d'UN clone, à partir d'événements déjà chargés
    (ex. `EventRepository.recent()`). Filtre sur `context["clone_id"]`, la
    convention posée par `AgentRuntime._record_event`."""
    return [e.to_dict() for e in events if e.context.get("clone_id") == clone_id]
