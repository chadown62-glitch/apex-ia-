"""Modèle de domaine d'un membre de population — dataclass pure, aucune DB.

Une « population » n'est pas un objet séparé : c'est l'ensemble des
`PopulationMember` qui partagent `specialty` (voir `apex.db.models.
PopulationMemberRow` pour la justification complète de ce choix). Ce module
ne définit donc PAS de classe `Population` — en ajouter une distincte de ce
qui se déduit déjà d'une requête serait exactement le doublon qu'ADR-0007
interdit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apex.db.models import PopulationMemberRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemberStatus(str, Enum):
    #: Vient d'être créé/évalué, n'a pas encore été départagé par la sélection.
    EXPERIMENTAL = "experimental"
    #: Retenu par la sélection (§9/§10 de la mission) — base possible pour
    #: un fork/une mutation future.
    ELITE = "elite"
    #: Le meilleur individu actuel selon la politique de fitness — jamais
    #: remplacé par un individu moins performant d'une génération suivante
    #: (`PopulationService.set_status` l'impose).
    CHAMPION = "champion"
    #: Stagnant/coûteux/échoue régulièrement (§11) — conservé, jamais
    #: silencieusement supprimé.
    HIBERNATING = "hibernating"
    #: Écarté définitivement de la population active — historique/lignage
    #: préservés, terminal (aucune transition sortante).
    RETIRED = "retired"


#: `RETIRED` est le seul état sans transition sortante — même patron que
#: `CloneState.is_terminal` (`apex.agents.model`).
_TERMINAL_STATUSES = frozenset({MemberStatus.RETIRED})


@dataclass
class PopulationMember:
    id: str
    specialty: str
    generation: int
    clone_id: str
    genome_version_id: str
    parent_member_id: str | None
    status: MemberStatus
    created_by: str
    experiment_ref: str
    fitness: float | None = None
    fitness_components: dict[str, Any] | None = None
    skill_candidate_id: str | None = None
    genome_candidate_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    decided_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @classmethod
    def new(
        cls,
        *,
        specialty: str,
        generation: int,
        clone_id: str,
        genome_version_id: str,
        parent_member_id: str | None,
        created_by: str,
        experiment_ref: str,
    ) -> PopulationMember:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, generation=generation, clone_id=clone_id,
            genome_version_id=genome_version_id, parent_member_id=parent_member_id,
            status=MemberStatus.EXPERIMENTAL, created_by=created_by, experiment_ref=experiment_ref,
        )

    @classmethod
    def from_row(cls, row: "PopulationMemberRow") -> PopulationMember:
        return cls(
            id=row.id, specialty=row.specialty, generation=row.generation, clone_id=row.clone_id,
            genome_version_id=row.genome_version_id, parent_member_id=row.parent_member_id,
            status=MemberStatus(row.status), created_by=row.created_by, experiment_ref=row.experiment_ref,
            fitness=row.fitness,
            fitness_components=dict(row.fitness_components) if row.fitness_components else None,
            skill_candidate_id=row.skill_candidate_id, genome_candidate_id=row.genome_candidate_id,
            created_at=row.created_at, decided_at=row.decided_at,
        )
