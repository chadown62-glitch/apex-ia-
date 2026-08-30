"""MemoryService — écriture/lecture de connaissances avec provenance.

Tranche minimale et réelle de l'étape 9 (MemoryProvider, registre :
PLANNED -> STUB). Réutilise entièrement le PolicyEngine existant — AUCUNE
règle de permission en dur ici, et **aucune modification de
`apex.policy`** : les trois actions nécessaires (`WRITE_OWN_MEMORY`,
`READ_SHARED_MEMORY`, `PROMOTE_MEMORY_TO_SHARED`) sont déjà déclarées, déjà
allouées par sujet dans `DEFAULT_ALLOWLIST`, déjà testées (notamment
`test_promote_memory_to_shared_is_granted_to_no_one_by_default`, qui garantit
que `promote_to_shared` échoue pour tout sujet tant que l'allow-list par
défaut n'est pas explicitement étendue).

**Immuabilité** : `write()` insère toujours une ligne neuve. `revise()`
insère une NOUVELLE ligne (`supersedes_id`) et refuse de réviser un
enregistrement déjà révisé (chaîne de révisions strictement linéaire — pas
de branchement dans cette tranche minimale). `promote_to_shared()` est la
SEULE mutation en place, et porte uniquement sur la visibilité — jamais sur
le contenu, la provenance ou la confiance de la connaissance elle-même.

**Aucune dépendance vers `apex.scheduler`, `apex.sandbox`, `apex.agents`,
`apex.missions`** — ce service est appelable directement, comme
`MissionService` l'était avant l'étape 7. Le raccordement « une mission
alimente automatiquement la mémoire » reste une extension future, hors
périmètre de cette étape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.memory.model import (
    MemoryKind,
    MemoryRecord,
    MemoryRelation,
    MemoryVisibility,
    RelationType,
    VerificationStatus,
)
from apex.memory.repository import MemoryRepository
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = MemoryRepository(session_factory)

    # -- écriture ------------------------------------------------------------

    def write(
        self,
        *,
        kind: MemoryKind,
        content: str,
        source: str,
        created_by: Subject,
        domain: str | None = None,
        confidence: float = 0.5,
        verification_status: VerificationStatus = VerificationStatus.UNCERTAIN,
        evidence_for: tuple[str, ...] = (),
        evidence_against: tuple[str, ...] = (),
        external_provenance: bool = False,
        external_source: str | None = None,
    ) -> MemoryRecord:
        self.policy.authorize(created_by, Action.WRITE_OWN_MEMORY)
        record = MemoryRecord.new(
            kind=kind, content=content, source=source, created_by=created_by.value, domain=domain,
            confidence=confidence, verification_status=verification_status,
            evidence_for=evidence_for, evidence_against=evidence_against,
            external_provenance=external_provenance, external_source=external_source,
        )
        self._repo.create_record(record)
        self._record_event(record.id, "memory.written", {"record_kind": record.kind.value})
        self._record_audit(actor=created_by.value, action="memory.written", target=record.id, details={})
        return record

    def revise(
        self,
        record_id: str,
        *,
        requested_by: Subject,
        content: str,
        source: str,
        confidence: float = 0.5,
        verification_status: VerificationStatus = VerificationStatus.UNCERTAIN,
        evidence_for: tuple[str, ...] = (),
        evidence_against: tuple[str, ...] = (),
        external_provenance: bool = False,
        external_source: str | None = None,
    ) -> MemoryRecord:
        """Crée une NOUVELLE ligne qui supersède `record_id` — jamais une
        mutation de la ligne existante. Refuse si `record_id` a déjà un
        successeur : la chaîne de révisions reste strictement linéaire dans
        cette tranche minimale (pas de branchement)."""
        self.policy.authorize(requested_by, Action.WRITE_OWN_MEMORY)
        original = self._repo.get_record(record_id)
        if original is None:
            raise LookupError(f"MemoryRecord « {record_id} » introuvable")
        if self._repo.has_successor(record_id):
            raise ValueError(
                f"MemoryRecord « {record_id} » a déjà été révisé — chaîne de révisions linéaire, "
                "pas de branchement"
            )
        revised = MemoryRecord.new(
            kind=original.kind, content=content, source=source, created_by=requested_by.value,
            domain=original.domain, confidence=confidence, verification_status=verification_status,
            evidence_for=evidence_for, evidence_against=evidence_against,
            external_provenance=external_provenance, external_source=external_source,
            supersedes_id=record_id,
        )
        self._repo.create_record(revised)
        self._record_event(
            revised.id, "memory.revised", {"supersedes_id": record_id},
        )
        self._record_audit(
            actor=requested_by.value, action="memory.revised", target=revised.id,
            details={"supersedes_id": record_id},
        )
        return revised

    def promote_to_shared(self, record_id: str, *, requested_by: Subject) -> MemoryRecord:
        """Acte explicite et audité — jamais un droit par défaut (§2.5).
        `Action.PROMOTE_MEMORY_TO_SHARED` n'est accordée à personne dans
        `DEFAULT_ALLOWLIST` : cet appel échoue pour tout sujet tant qu'une
        allow-list personnalisée ne l'accorde pas explicitement."""
        self.policy.authorize(requested_by, Action.PROMOTE_MEMORY_TO_SHARED)
        record = self._repo.get_record(record_id)
        if record is None:
            raise LookupError(f"MemoryRecord « {record_id} » introuvable")
        promoted = self._repo.promote_to_shared(record_id)
        if not promoted:
            raise ValueError(
                f"MemoryRecord « {record_id} » n'est plus OWN (déjà promu, ou état inattendu) "
                "— promotion refusée"
            )
        self._record_event(record_id, "memory.promoted_to_shared", {})
        self._record_audit(
            actor=requested_by.value, action="memory.promoted_to_shared", target=record_id, details={},
        )
        updated = self._repo.get_record(record_id)
        assert updated is not None
        return updated

    # -- lecture -------------------------------------------------------------

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._repo.get_record(record_id)

    def read_shared(self, *, requested_by: Subject, limit: int = 200) -> list[MemoryRecord]:
        self.policy.authorize(requested_by, Action.READ_SHARED_MEMORY)
        return self._repo.list_records(visibility=MemoryVisibility.SHARED.value, limit=limit)

    # -- relations SUPPORTS/CONTRADICTS --------------------------------------

    def relate(
        self,
        *,
        source_id: str,
        target_id: str,
        relation: RelationType,
        provenance: str,
        requested_by: Subject,
        confidence: float | None = None,
    ) -> MemoryRelation:
        """Relie deux `MemoryRecord` EXISTANTS — jamais une référence
        pendante. Réutilise `Action.WRITE_OWN_MEMORY` (créer un lien est un
        acte d'écriture mémoire comme un autre, pas une catégorie séparée —
        aucune nouvelle action PolicyEngine introduite par cette étape)."""
        self.policy.authorize(requested_by, Action.WRITE_OWN_MEMORY)
        if self._repo.get_record(source_id) is None:
            raise LookupError(f"MemoryRecord source « {source_id} » introuvable")
        if self._repo.get_record(target_id) is None:
            raise LookupError(f"MemoryRecord cible « {target_id} » introuvable")
        rel = MemoryRelation.new(
            source_id=source_id, target_id=target_id, relation=relation, provenance=provenance,
            created_by=requested_by.value, confidence=confidence,
        )
        self._repo.create_relation(rel)
        self._record_event(
            rel.id, "memory.related",
            {"source_id": source_id, "target_id": target_id, "relation": relation.value},
        )
        self._record_audit(
            actor=requested_by.value, action="memory.related", target=rel.id,
            details={"source_id": source_id, "target_id": target_id, "relation": relation.value},
        )
        return rel

    def list_relations_for(self, record_id: str, limit: int = 200) -> list[MemoryRelation]:
        return self._repo.list_relations_for(record_id, limit=limit)

    # -- internes -----------------------------------------------------------

    def _record_event(self, subject_id: str, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.memory",
                msg=kind, context={"memory_id": subject_id, "kind": kind, **details},
            )

    def _record_audit(self, *, actor: str, action: str, target: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        # Écrit pour le compte de l'acteur réel — même règle que
        # MissionService._record_audit : l'acteur porte déjà son propre
        # droit d'audit via l'action mémoire qu'il vient d'exercer.
        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=target, details=details,
            )
