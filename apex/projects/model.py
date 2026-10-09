"""Modèle de domaine d'un Project — dataclass pure, aucune dépendance DB
(même séparation qu'`apex.missions.model.Mission` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apex.db.models import ProjectRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Project:
    id: str
    name: str
    description: str
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)
    #: Consignes jointes à chaque tour des conversations du projet (0.4.0) ; vide : aucune.
    instructions: str = ""
    updated_at: datetime | None = None

    @classmethod
    def new(cls, *, name: str, description: str, created_by: str, instructions: str = "") -> Project:
        return cls(id=str(uuid.uuid4()), name=name, description=description, created_by=created_by,
                   instructions=instructions)

    @classmethod
    def from_row(cls, row: "ProjectRow") -> Project:
        return cls(
            id=row.id, name=row.name, description=row.description,
            created_by=row.created_by, created_at=row.created_at,
            instructions=getattr(row, "instructions", None) or "", updated_at=getattr(row, "updated_at", None),
        )
