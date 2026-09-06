"""`BugRecord` — suivi RÉEL d'un bug observé (passe « Adversarial
Curriculum + Capability Benchmarking », N+10, §7 de la mission).

Dérivé EXCLUSIVEMENT de `apex.metalearner.weakness_engine.
WeaknessEngineService.aggregate` (N+9, INCHANGÉ) — jamais un bug inventé :
`weakness_id` (`apex.metalearner.challenge_model.weakness_key`) référence
la MÊME faiblesse agrégée, `first_seen_generation`/`first_seen_at`/
`member_id`/`genome_version_id`/`evidence_reference` proviennent
directement des champs déjà réels de `GauntletWeakness`. `status` est la
SEULE colonne mutable de cette passe : `OPEN` -> `FIXED` (quand
`ImprovementStatus.RESOLVED`, N+9, inchangé) -> `REGRESSED` (si la MÊME
faiblesse réapparaît après avoir été `FIXED`) -> `FIXED` de nouveau
possible — voir `apex.metalearner.bug_farm_service` pour les règles de
transition EXACTES."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

if TYPE_CHECKING:
    from apex.db.models import BugRecordRow


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BugStatus(str, Enum):
    OPEN = "open"
    FIXED = "fixed"
    REGRESSED = "regressed"


@dataclass(frozen=True)
class BugRecord:
    id: str
    specialty: str
    weakness_id: str
    dimension: JudgeDimension
    failure_category: str
    severity: JudgeSeverity
    first_seen_generation: int
    first_seen_at: datetime
    member_id: str
    genome_version_id: str
    evidence_reference: str | None
    status: BugStatus
    fixed_at_generation: int | None
    regressed_at_generation: int | None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        specialty: str,
        weakness_id: str,
        dimension: JudgeDimension,
        failure_category: str,
        severity: JudgeSeverity,
        first_seen_generation: int,
        first_seen_at: datetime,
        member_id: str,
        genome_version_id: str,
        evidence_reference: str | None,
    ) -> BugRecord:
        now = _utcnow()
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, weakness_id=weakness_id, dimension=dimension,
            failure_category=failure_category, severity=severity,
            first_seen_generation=first_seen_generation, first_seen_at=first_seen_at,
            member_id=member_id, genome_version_id=genome_version_id,
            evidence_reference=evidence_reference, status=BugStatus.OPEN, fixed_at_generation=None,
            regressed_at_generation=None, created_at=now, updated_at=now,
        )

    @classmethod
    def from_row(cls, row: BugRecordRow) -> BugRecord:
        return cls(
            id=row.id, specialty=row.specialty, weakness_id=row.weakness_id,
            dimension=JudgeDimension(row.dimension), failure_category=row.failure_category,
            severity=JudgeSeverity(row.severity), first_seen_generation=row.first_seen_generation,
            first_seen_at=row.first_seen_at, member_id=row.member_id,
            genome_version_id=row.genome_version_id,
            evidence_reference=row.evidence_reference, status=BugStatus(row.status),
            fixed_at_generation=row.fixed_at_generation,
            regressed_at_generation=row.regressed_at_generation,
            created_at=row.created_at, updated_at=row.updated_at,
        )
