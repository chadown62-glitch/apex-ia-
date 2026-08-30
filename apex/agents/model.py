"""Modèle de domaine d'un clone — état, transitions, identité.

Aucune dépendance DB ici : c'est la même séparation que `apex.gate.model`
(dataclasses pures) vis-à-vis d'`apex.db.models` (SQLAlchemy). Ce module peut
être testé et raisonné sans base de données.

Le type d'un clone réutilise `apex.policy.model.Subject` — il n'existe pas de
`CloneType` séparé. Un `Subject` a déjà neuf valeurs de clone plus `CORE`/
`HUMAN` ; dupliquer ce catalogue ailleurs serait exactement le doublon
qu'ADR-0007 interdit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from apex.policy.model import Subject

if TYPE_CHECKING:
    # Import différé — apex.db a le droit de rester indépendant de sqlalchemy
    # à l'import (voir apex/db/event_sink.py) ; AgentRow n'est utilisé qu'en
    # annotation de type ici.
    from apex.db.models import AgentRow

#: Sujets valides comme *type* d'un clone — CORE et HUMAN ne sont jamais le
#: type d'un clone : ce sont ceux qui en spawnent un (voir engine.py).
CLONE_SUBJECTS: frozenset[Subject] = frozenset(Subject) - {Subject.CORE, Subject.HUMAN}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CloneState(str, Enum):
    SPAWNING = "spawning"
    READY = "ready"
    WORKING = "working"
    REPORTING = "reporting"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    CRASHED = "crashed"
    RECOVERING = "recovering"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (CloneState.TERMINATED, CloneState.FAILED)


#: Machine à états exacte de docs/COGNITIVE_ARCHITECTURE.md §2.3. Toute
#: transition absente d'ici est un bug du Core, jamais une action d'un clone
#: (un clone ne pilote pas sa propre machine à états — le superviseur le fait).
_TRANSITIONS: dict[CloneState, frozenset[CloneState]] = {
    CloneState.SPAWNING: frozenset({CloneState.READY, CloneState.FAILED}),
    CloneState.READY: frozenset({CloneState.WORKING, CloneState.TERMINATING}),
    CloneState.WORKING: frozenset({CloneState.REPORTING, CloneState.CRASHED, CloneState.TERMINATING}),
    CloneState.REPORTING: frozenset({CloneState.TERMINATED}),
    CloneState.TERMINATING: frozenset({CloneState.TERMINATED}),
    CloneState.TERMINATED: frozenset(),
    CloneState.CRASHED: frozenset({CloneState.RECOVERING, CloneState.FAILED}),
    CloneState.RECOVERING: frozenset({CloneState.SPAWNING}),
    CloneState.FAILED: frozenset(),
}


class InvalidTransition(ValueError):
    """Transition d'état demandée qui n'existe pas dans la machine à états.

    Distinct de `PolicyDenied` : ceci est un bug d'appelant (le Core lui-même
    se trompe), pas un refus d'autorisation.
    """


@dataclass
class Clone:
    """Représentation en mémoire d'un clone. La persistance (`AgentRow`,
    `apex.db.models`) est une projection de cet objet, jamais l'inverse."""

    clone_id: str
    type: Subject
    state: CloneState
    genome_ref: str
    spawned_by: str
    pool_id: str
    isolation_level: str
    mission_id: str | None = None
    max_retries: int = 2
    retries_used: int = 0
    last_error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    terminated_at: datetime | None = None
    last_heartbeat_at: datetime | None = None

    @classmethod
    def new(
        cls,
        clone_type: Subject,
        *,
        genome_ref: str,
        spawned_by: str,
        pool_id: str,
        isolation_level: str,
        mission_id: str | None = None,
        max_retries: int = 2,
    ) -> Clone:
        return cls(
            clone_id=str(uuid.uuid4()),
            type=clone_type,
            state=CloneState.SPAWNING,
            genome_ref=genome_ref,
            spawned_by=spawned_by,
            pool_id=pool_id,
            isolation_level=isolation_level,
            mission_id=mission_id,
            max_retries=max_retries,
        )

    @classmethod
    def from_row(cls, row: "AgentRow") -> Clone:
        """Reconstruit un `Clone` depuis sa persistance (`AgentRow`).

        Ne vit qu'ici, pas dans `apex.db` : la couche de persistance reste
        indépendante des modules de domaine (`apex.db` n'importe jamais
        `apex.agents`), c'est le module de domaine qui sait lire sa propre
        forme persistée.
        """
        return cls(
            clone_id=row.clone_id,
            type=Subject(row.type),
            state=CloneState(row.state),
            genome_ref=row.genome_ref,
            spawned_by=row.spawned_by,
            pool_id=row.pool_id,
            isolation_level=row.isolation_level,
            mission_id=row.mission_id,
            max_retries=row.max_retries,
            retries_used=row.retries_used,
            last_error=row.last_error,
            created_at=row.created_at,
            started_at=row.started_at,
            terminated_at=row.terminated_at,
            last_heartbeat_at=row.last_heartbeat_at,
        )

    def transition(self, new_state: CloneState) -> None:
        """Mute l'état en place si la transition est légale, lève sinon.

        En place (pas de nouvel objet) car l'identité du clone (`clone_id`)
        ne change jamais — seul son état évolue, comme `GateCheckResult` reste
        associé au même `check_id` tout au long d'une exécution.
        """
        allowed = _TRANSITIONS[self.state]
        if new_state not in allowed:
            raise InvalidTransition(
                f"transition « {self.state.value} » -> « {new_state.value} » invalide "
                f"pour le clone {self.clone_id} (autorisées : "
                f"{sorted(s.value for s in allowed) or 'aucune — état terminal'})"
            )
        self.state = new_state
