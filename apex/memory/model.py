"""Modèle de domaine de MemoryProvider — dataclasses pures, aucune
dépendance DB (même séparation que `apex.missions.model` vis-à-vis
d'`apex.db.models`).

Tranche minimale et réelle de l'étape 9 (docs/COGNITIVE_ARCHITECTURE.md §6,
§0 — MemoryProvider) : un enregistrement (`MemoryRecord`) porte sa
provenance, sa confiance et son statut de vérification ; une relation
(`MemoryRelation`) relie deux enregistrements existants par SUPPORTS ou
CONTRADICTS. Aucun graphe visuel, aucun RAG/embeddings, aucune recherche
sémantique — une structure de données, rien de plus (consigne étape 9).

**Immuabilité délibérée** : ni `MemoryRecord` ni `MemoryRelation` n'ont de
méthode de mutation en place. Réviser une connaissance crée une NOUVELLE
ligne avec `supersedes_id` pointant vers l'ancienne — jamais un UPDATE. Une
relation, une fois créée, n'est ni modifiée ni supprimée. C'est ce qui donne
« l'historique des révisions » sans table séparée, même logique que
l'`ArtifactStore` adressé par contenu (PLANNED) et le futur `GenomeStore`
versionné (PLANNED).

**Quatre vocabulaires fermés, jamais fusionnés** (consigne étape 9) :
- `confidence: float` — probabiliste, dans [0, 1].
- `VerificationStatus` — épistémique : cette AFFIRMATION est-elle vraie ?
- `MemoryVisibility` — portée d'accès : OWN (privée) ou SHARED (promue).
- `apex.obs.status.DataStatus` (REAL/SIMULATED/UNKNOWN) — répond à une
  question différente (« cette donnée AFFICHÉE est-elle mesurée ? ») et
  n'apparaît PAS ici : un `MemoryRecord` est toujours une donnée réellement
  écrite, la question DataStatus ne se pose qu'à l'affichage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import différé — apex.memory doit rester importable sans l'extra `db`
    # installé, même garde que apex.missions/apex.agents.
    from apex.db.models import MemoryRecordRow, MemoryRelationRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryKind(str, Enum):
    """Nature du souvenir — UNE seule table (schéma de provenance/confiance/
    vérification identique pour les quatre), mais les concepts eux-mêmes ne
    sont jamais fusionnés (consigne étape 9, point 6) : `kind` distingue la
    nature, il ne mélange rien."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    EXPERIMENTAL = "experimental"


class VerificationStatus(str, Enum):
    """Statut épistémique d'une affirmation — distinct de `confidence`
    (probabiliste) et de tout autre vocabulaire fermé du dépôt. Une absence
    de preuve reste `UNCERTAIN`, jamais promue à `VERIFIED` par défaut."""

    VERIFIED = "verified"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    DISPUTED = "disputed"
    FALSE = "false"


class MemoryVisibility(str, Enum):
    """Portée d'accès — reflète directement le vocabulaire déjà déclaré par
    PolicyEngine (`Action.WRITE_OWN_MEMORY` / `READ_SHARED_MEMORY` /
    `PROMOTE_MEMORY_TO_SHARED`, `apex.policy.model`). OWN à la création,
    SHARED uniquement par un acte explicite et audité — jamais un droit par
    défaut (§2.5 de docs/COGNITIVE_ARCHITECTURE.md, déjà appliqué par
    `PolicyEngine` : `PROMOTE_MEMORY_TO_SHARED` n'est accordée à personne
    par défaut)."""

    OWN = "own"
    SHARED = "shared"


class RelationType(str, Enum):
    """Arête du graphe de connaissances minimal (consigne étape 9) — aucun
    moteur de traversée, aucune visualisation : une structure de données
    entre deux `MemoryRecord` EXISTANTS, rien de plus."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


@dataclass
class MemoryRecord:
    """Une connaissance, avec sa provenance complète. Jamais mutée en place
    après création — voir la docstring du module."""

    id: str
    kind: MemoryKind
    domain: str | None
    content: str
    source: str
    created_by: str
    confidence: float
    verification_status: VerificationStatus
    visibility: MemoryVisibility
    evidence_for: tuple[str, ...]
    evidence_against: tuple[str, ...]
    external_provenance: bool
    external_source: str | None
    supersedes_id: str | None
    expires_at: datetime | None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        kind: MemoryKind,
        content: str,
        source: str,
        created_by: str,
        domain: str | None = None,
        confidence: float = 0.5,
        verification_status: VerificationStatus = VerificationStatus.UNCERTAIN,
        evidence_for: tuple[str, ...] = (),
        evidence_against: tuple[str, ...] = (),
        external_provenance: bool = False,
        external_source: str | None = None,
        supersedes_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord:
        if not content.strip():
            raise ValueError("le contenu d'un MemoryRecord est obligatoire")
        if not source.strip():
            raise ValueError("la source d'un MemoryRecord est obligatoire — jamais anonyme")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence doit être dans [0, 1] — reçu {confidence}")
        return cls(
            id=str(uuid.uuid4()),
            kind=kind,
            domain=domain,
            content=content,
            source=source,
            created_by=created_by,
            confidence=confidence,
            verification_status=verification_status,
            visibility=MemoryVisibility.OWN,
            evidence_for=tuple(evidence_for),
            evidence_against=tuple(evidence_against),
            external_provenance=external_provenance,
            external_source=external_source,
            supersedes_id=supersedes_id,
            expires_at=expires_at,
        )

    @classmethod
    def from_row(cls, row: "MemoryRecordRow") -> MemoryRecord:
        return cls(
            id=row.id,
            kind=MemoryKind(row.kind),
            domain=row.domain,
            content=row.content,
            source=row.source,
            created_by=row.created_by,
            confidence=row.confidence,
            verification_status=VerificationStatus(row.verification_status),
            visibility=MemoryVisibility(row.visibility),
            evidence_for=tuple(row.evidence_for or ()),
            evidence_against=tuple(row.evidence_against or ()),
            external_provenance=row.external_provenance,
            external_source=row.external_source,
            supersedes_id=row.supersedes_id,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )


@dataclass
class MemoryRelation:
    """SUPPORTS/CONTRADICTS entre deux `MemoryRecord` EXISTANTS — jamais
    mutée ni supprimée après création."""

    id: str
    source_id: str
    target_id: str
    relation: RelationType
    provenance: str
    created_by: str
    confidence: float | None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        source_id: str,
        target_id: str,
        relation: RelationType,
        provenance: str,
        created_by: str,
        confidence: float | None = None,
    ) -> MemoryRelation:
        if source_id == target_id:
            raise ValueError("une relation ne peut pas relier un MemoryRecord à lui-même")
        if not provenance.strip():
            raise ValueError("la provenance d'une relation est obligatoire — jamais anonyme")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence doit être dans [0, 1] — reçu {confidence}")
        return cls(
            id=str(uuid.uuid4()),
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            provenance=provenance,
            created_by=created_by,
            confidence=confidence,
        )

    @classmethod
    def from_row(cls, row: "MemoryRelationRow") -> MemoryRelation:
        return cls(
            id=row.id,
            source_id=row.source_id,
            target_id=row.target_id,
            relation=RelationType(row.relation),
            provenance=row.provenance,
            created_by=row.created_by,
            confidence=row.confidence,
            created_at=row.created_at,
        )
