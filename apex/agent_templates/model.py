"""Modèle de domaine d'un AgentTemplate — dataclass pure, aucune dépendance
DB (même séparation qu'`apex.missions.model.Mission` vis-à-vis d'`apex.db.
models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apex.db.models import AgentTemplateRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRole(str, Enum):
    """Vocabulaire fermé pour une DÉFINITION d'agent — délibérément DISTINCT
    d'`apex.policy.model.Subject`/`CloneType` (research/learning/developer/
    experiment/critic/security/benchmark/teacher/integrator) : un template
    décrit une intention métier (« un CODER »), pas encore un sujet
    PolicyEngine réel. Faire tourner un template exigerait de le relier à un
    `CloneType` réel au moment du spawn (`SPAWN_CLONE`, PLANNED pour cette
    passe) — cette table ne préjuge pas de ce mapping."""

    CODER = "coder"
    RESEARCHER = "researcher"
    TESTER = "tester"
    ANALYST = "analyst"
    DESIGNER = "designer"
    SECURITY = "security"
    EVALUATOR = "evaluator"
    #: Ajoutés passe « Agents V1 » — mêmes garanties : une fonction
    #: opérationnelle déclarée, jamais une personnalité artificielle.
    PROJECT_MANAGER = "project_manager"
    GENERALIST = "generalist"


class AgentTemplateStatus(str, Enum):
    #: Défini (créé, listable) — jamais encore spawnable réellement.
    DEFINED = "defined"


@dataclass
class AgentTemplate:
    """`model_role` référence un rôle d'`ApexConfig.model_roles` (ex.
    "fast"/"coder_main") par CONVENTION, jamais vérifié à la création (le
    catalogue de rôles vit dans `config.yaml`, pas ici — même simplification
    documentée qu'`ModelRow.assigned_agent`). `tools` reste une liste
    déclarée, non appliquée : `ToolProvider` est lui-même `PLANNED`."""

    id: str
    name: str
    role: AgentRole
    model_role: str | None
    tools: tuple[str, ...]
    description: str
    status: AgentTemplateStatus
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        role: AgentRole,
        description: str,
        created_by: str,
        model_role: str | None = None,
        tools: tuple[str, ...] = (),
    ) -> AgentTemplate:
        return cls(
            id=str(uuid.uuid4()), name=name, role=role, model_role=model_role, tools=tools,
            description=description, status=AgentTemplateStatus.DEFINED, created_by=created_by,
        )

    @classmethod
    def from_row(cls, row: "AgentTemplateRow") -> AgentTemplate:
        return cls(
            id=row.id, name=row.name, role=AgentRole(row.role), model_role=row.model_role,
            tools=tuple(row.tools or ()), description=row.description,
            status=AgentTemplateStatus(row.status), created_by=row.created_by, created_at=row.created_at,
        )
