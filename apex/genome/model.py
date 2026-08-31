"""Modèle de domaine d'une GenomeVersion — dataclass pure, aucune DB.

Registre : `GenomeStore`, « configuration mutable versionnée » (`phase_defined`
4, `phase_implemented` 17). Le CONTENU (dict JSON-sérialisable — « données
versionnées mutables, jamais de code », `docs/COGNITIVE_ARCHITECTURE.md` §1)
n'est PAS un champ de cette dataclass : il vit dans `ArtifactStoreProvider`,
adressé par `content_hash` (exactement `genome_ref: hash` de `Clone`, §2.2).
`GenomeVersion` ne porte que les métadonnées — même séparation que
`apex.artifacts.model.ArtifactMetadata` vis-à-vis du contenu qu'il décrit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apex.db.models import GenomeVersionRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GenomeVersionStatus(str, Enum):
    #: Proposée avec contenu déjà stocké, en attente d'une décision HUMAINE.
    PROPOSED = "proposed"
    #: Promue — décision HUMAINE, unique, jamais redécidée.
    PROMOTED = "promoted"
    #: Rejetée — décision HUMAINE, unique, jamais redécidée.
    REJECTED = "rejected"


class GenomeVersionOrigin(str, Enum):
    #: Saisie/assemblée directement (ex. genèse, ou proposition humaine).
    MANUAL = "manual"
    #: Produite par `apex.genome.mutation.mutate()` — un seul parent.
    MUTATION = "mutation"
    #: Produite par `apex.genome.mutation.crossover()` — deux parents.
    CROSSOVER = "crossover"


@dataclass
class GenomeVersion:
    id: str
    content_hash: str
    origin: GenomeVersionOrigin
    status: GenomeVersionStatus
    parent_version_id: str | None
    parent_version_id_b: str | None
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None

    @classmethod
    def new(
        cls, *, content_hash: str, origin: GenomeVersionOrigin, created_by: str,
        parent_version_id: str | None = None, parent_version_id_b: str | None = None,
    ) -> GenomeVersion:
        return cls(
            id=str(uuid.uuid4()), content_hash=content_hash, origin=origin,
            status=GenomeVersionStatus.PROPOSED, parent_version_id=parent_version_id,
            parent_version_id_b=parent_version_id_b, created_by=created_by,
        )

    @classmethod
    def from_row(cls, row: "GenomeVersionRow") -> GenomeVersion:
        return cls(
            id=row.id, content_hash=row.content_hash, origin=GenomeVersionOrigin(row.origin),
            status=GenomeVersionStatus(row.status), parent_version_id=row.parent_version_id,
            parent_version_id_b=row.parent_version_id_b, created_by=row.created_by,
            created_at=row.created_at, decided_by=row.decided_by, decided_at=row.decided_at,
            decision_reason=row.decision_reason,
        )
