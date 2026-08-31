"""Modèle de domaine d'une Skill — dataclass pure, aucune dépendance DB
(même séparation qu'`apex.missions.model.Mission` vis-à-vis d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import différé — même raison que Mission.from_row : apex.skills doit
    # rester importable sans l'extra `db` installé.
    from apex.db.models import SkillRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillStatus(str, Enum):
    #: Proposée avec preuve, en attente d'une décision HUMAINE.
    PROPOSED = "proposed"
    #: Promue — décision HUMAINE, unique, jamais redécidée.
    ACQUIRED = "acquired"
    #: Rejetée — décision HUMAINE, unique, jamais redécidée.
    REJECTED = "rejected"


@dataclass
class Skill:
    """Une compétence versionnée, distincte du Genome
    (`docs/VISION_INTEGRATION.md` §2.2). `prerequisite_skill_ids` forme un
    graphe de prérequis (vérifié acyclique à la proposition, réutilise
    `apex.missions.dependencies.detect_cycle` — aucune logique dupliquée) ;
    une compétence ne peut être promue que si tous ses prérequis sont déjà
    `ACQUIRED` (vérifié à la décision, `apex.skills.service`).
    `evidence_run_ids` référence des `apex.evalrunner.model.EvalRun`
    existants — jamais une preuve inventée. `supersedes_id` porte la
    généalogie (révision = nouvelle ligne, jamais une mutation du contenu).
    """

    id: str
    name: str
    specialty: str
    description: str
    status: SkillStatus
    prerequisite_skill_ids: tuple[str, ...]
    evidence_run_ids: tuple[str, ...]
    confidence: float | None
    supersedes_id: str | None
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None

    @classmethod
    def new(
        cls,
        *,
        name: str,
        specialty: str,
        description: str,
        created_by: str,
        prerequisite_skill_ids: tuple[str, ...] = (),
        evidence_run_ids: tuple[str, ...] = (),
        confidence: float | None = None,
        supersedes_id: str | None = None,
    ) -> Skill:
        return cls(
            id=str(uuid.uuid4()),
            name=name,
            specialty=specialty,
            description=description,
            status=SkillStatus.PROPOSED,
            prerequisite_skill_ids=tuple(prerequisite_skill_ids),
            evidence_run_ids=tuple(evidence_run_ids),
            confidence=confidence,
            supersedes_id=supersedes_id,
            created_by=created_by,
        )

    @classmethod
    def from_row(cls, row: "SkillRow") -> Skill:
        return cls(
            id=row.id,
            name=row.name,
            specialty=row.specialty,
            description=row.description,
            status=SkillStatus(row.status),
            prerequisite_skill_ids=tuple(row.prerequisite_skill_ids or ()),
            evidence_run_ids=tuple(row.evidence_run_ids or ()),
            confidence=row.confidence,
            supersedes_id=row.supersedes_id,
            created_by=row.created_by,
            created_at=row.created_at,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            decision_reason=row.decision_reason,
        )
