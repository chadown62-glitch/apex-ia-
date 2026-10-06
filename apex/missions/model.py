"""Modèle de domaine d'une Mission — dataclass pure, aucune dépendance DB
(même séparation que `apex.agents.model.Clone` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from apex.missions.states import MissionState, assert_transition_allowed

if TYPE_CHECKING:
    # Import différé — même raison que Clone.from_row : apex.missions doit
    # rester importable sans l'extra `db` installé.
    from apex.db.models import MissionRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MissionPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ValidationStatus(str, Enum):
    """Distinct de `MissionState` : répond à "quelle décision humaine a été
    prise", pas "où en est la mission". `None` tant qu'aucune décision n'a
    encore été demandée (avant REVIEW) ou rendue (pendant REVIEW)."""

    VALIDATED = "validated"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


@dataclass
class Mission:
    id: str
    title: str
    description: str
    specialty: str
    objective: str
    priority: MissionPriority
    constraints: str | None
    assigned_agent: str | None
    state: MissionState
    result: str | None
    error: str | None
    validation_status: ValidationStatus | None
    created_by: str
    #: Étape 8 — IDs d'autres missions devant être COMPLETED avant que
    #: celle-ci soit éligible au Scheduler. Fixé une fois pour toutes à la
    #: création (voir apex.missions.dependencies pour l'invariant d'absence
    #: de cycle que cette immuabilité garantit).
    depends_on: tuple[str, ...] = ()
    #: Passe « Agents V1 » — référence un `apex.agent_registry.model.Agent`,
    #: délibérément DISTINCT d'`assigned_agent` ci-dessus (un clone OS-level
    #: réel). Voir `apex.db.models.MissionRow.agent_id` pour le motif complet.
    agent_id: str | None = None
    #: Unification Missions/TaskLoop (06/10/2026) : Task vérifiable exécutée par le chemin autonome canonique
    #: (`apex.missions.execution.run_mission_task` -> `apex.taskloop`), options EXPLICITES de cette exécution
    #: (`apex.missions.task_options`) et TaskRun réellement lancée. `None` : chemins historiques.
    task_id: str | None = None
    task_options: dict[str, Any] | None = None
    task_run_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def new(
        cls,
        *,
        title: str,
        description: str,
        specialty: str,
        objective: str,
        created_by: str,
        priority: MissionPriority = MissionPriority.NORMAL,
        constraints: str | None = None,
        assigned_agent: str | None = None,
        agent_id: str | None = None,
        depends_on: tuple[str, ...] = (),
        task_id: str | None = None,
        task_options: dict[str, Any] | None = None,
    ) -> Mission:
        return cls(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            specialty=specialty,
            objective=objective,
            priority=priority,
            constraints=constraints,
            assigned_agent=assigned_agent,
            agent_id=agent_id,
            state=MissionState.DRAFT,
            result=None,
            error=None,
            validation_status=None,
            created_by=created_by,
            depends_on=depends_on,
            task_id=task_id,
            task_options=task_options,
        )

    @classmethod
    def from_row(cls, row: "MissionRow") -> Mission:
        return cls(
            id=row.id,
            title=row.title,
            description=row.description,
            specialty=row.specialty,
            objective=row.objective,
            priority=MissionPriority(row.priority),
            constraints=row.constraints,
            assigned_agent=row.assigned_agent,
            agent_id=row.agent_id,
            state=MissionState(row.state),
            result=row.result,
            error=row.error,
            validation_status=ValidationStatus(row.validation_status) if row.validation_status else None,
            created_by=row.created_by,
            depends_on=tuple(row.depends_on or ()),
            task_id=getattr(row, "task_id", None),
            task_options=getattr(row, "task_options", None),
            task_run_id=getattr(row, "task_run_id", None),
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    def transition(self, new_state: MissionState) -> None:
        """Mute l'état en place si la transition est légale, lève sinon —
        même contrat que `Clone.transition`."""
        assert_transition_allowed(self.state, new_state)
        self.state = new_state
