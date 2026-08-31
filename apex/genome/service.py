"""GenomeStoreService — proposition, promotion, rejet d'une version de Genome.

Registre : `GenomeStore`, phase 17 — « configuration mutable versionnée ».
Compose `ArtifactStoreProvider` (STUB réel depuis la Phase 10) pour le
contenu (adressé par contenu, jamais réimplémenté ici) et suit EXACTEMENT
le patron `SkillRegistryService` (Phase 14) pour le cycle de décision :
`Action.PROPOSE_GENOME_VERSION` accordée à CORE ET HUMAIN,
`PROMOTE_GENOME_VERSION`/`REJECT_GENOME_VERSION` HUMAIN SEUL — invariant
absolu (ADR-0001) : aucun clone, aucun CORE seul ne peut faire évoluer le
Genome sans décision humaine.

**Ce module ne modifie jamais le Core** : il stocke des DONNÉES (un dict
JSON-sérialisable), jamais de code, ne déploie ni n'exécute rien. Le lien
entre une `GenomeVersion` promue et un comportement réel d'APEX reste
entièrement hors du périmètre de cette étape (aucun raccordement à
`AgentRuntime`/`Clone.genome_ref` — un futur pas, pas celui-ci).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from apex.artifacts.provider import ArtifactStoreProvider, get_default_provider
from apex.genome.model import GenomeVersion, GenomeVersionOrigin, GenomeVersionStatus
from apex.genome.repository import GenomeVersionRepository
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json_bytes(content: dict[str, Any]) -> bytes:
    """Sérialisation déterministe : mêmes données -> mêmes octets -> même
    `content_hash` (déduplication naturelle de l'adressage par contenu,
    même garantie qu'`ArtifactStoreProvider.put`)."""
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class GenomeStoreService:
    def __init__(
        self, session_factory: Any, *, artifact_store: ArtifactStoreProvider | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._artifacts = artifact_store or get_default_provider()
        self._repo = GenomeVersionRepository(session_factory)

    # -- proposition -----------------------------------------------------

    def propose(
        self,
        *,
        content: dict[str, Any],
        created_by: Subject,
        origin: GenomeVersionOrigin = GenomeVersionOrigin.MANUAL,
        parent_version_id: str | None = None,
        parent_version_id_b: str | None = None,
    ) -> GenomeVersion:
        self.policy.authorize(created_by, Action.PROPOSE_GENOME_VERSION)
        self._validate_content(content)
        if parent_version_id is not None:
            self._require(parent_version_id)
        if parent_version_id_b is not None:
            self._require(parent_version_id_b)

        metadata = self._artifacts.put(canonical_json_bytes(content), content_type="application/json")
        version = GenomeVersion.new(
            content_hash=metadata.content_hash, origin=origin, created_by=created_by.value,
            parent_version_id=parent_version_id, parent_version_id_b=parent_version_id_b,
        )
        self._repo.create(version)
        self._record_event(version, "genome_version.proposed", {})
        self._record_audit(
            version, actor=created_by.value, action="genome_version.proposed",
            details={"origin": origin.value, "parent_version_id": parent_version_id},
        )
        return version

    # -- décisions humaines ------------------------------------------------

    def promote(self, version_id: str, *, requested_by: Subject, reason: str | None = None) -> GenomeVersion:
        self.policy.authorize(requested_by, Action.PROMOTE_GENOME_VERSION)
        version = self._require(version_id)
        if version.status is not GenomeVersionStatus.PROPOSED:
            raise ValueError(
                f"version « {version_id} » déjà décidée ({version.status.value}) — double décision refusée"
            )
        decided = self._repo.decide(
            version_id, status=GenomeVersionStatus.PROMOTED, decided_by=requested_by.value,
            decided_at=_utcnow(), decision_reason=reason,
        )
        if decided is None:
            raise ValueError(f"version « {version_id} » déjà décidée entre-temps — course perdue")
        self._record_event(decided, "genome_version.promoted", {"requested_by": requested_by.value})
        self._record_audit(
            decided, actor=requested_by.value, action="genome_version.promoted", details={"reason": reason},
        )
        return decided

    def reject(self, version_id: str, *, requested_by: Subject, reason: str | None = None) -> GenomeVersion:
        self.policy.authorize(requested_by, Action.REJECT_GENOME_VERSION)
        version = self._require(version_id)
        if version.status is not GenomeVersionStatus.PROPOSED:
            raise ValueError(
                f"version « {version_id} » déjà décidée ({version.status.value}) — double décision refusée"
            )
        decided = self._repo.decide(
            version_id, status=GenomeVersionStatus.REJECTED, decided_by=requested_by.value,
            decided_at=_utcnow(), decision_reason=reason,
        )
        if decided is None:
            raise ValueError(f"version « {version_id} » déjà décidée entre-temps — course perdue")
        self._record_event(decided, "genome_version.rejected", {"requested_by": requested_by.value})
        self._record_audit(
            decided, actor=requested_by.value, action="genome_version.rejected", details={"reason": reason},
        )
        return decided

    # -- lecture -------------------------------------------------------------

    def get(self, version_id: str) -> GenomeVersion | None:
        return self._repo.get(version_id)

    def get_content(self, version_id: str) -> dict[str, Any]:
        version = self._require(version_id)
        raw = self._artifacts.get(version.content_hash)
        return json.loads(raw.decode("utf-8"))

    def list_all(self, limit: int = 200) -> list[GenomeVersion]:
        return self._repo.list_all(limit=limit)

    def get_current(self) -> GenomeVersion | None:
        return self._repo.get_current()

    def get_current_content(self) -> dict[str, Any] | None:
        current = self.get_current()
        return self.get_content(current.id) if current is not None else None

    # -- internes -----------------------------------------------------------

    def _validate_content(self, content: dict[str, Any]) -> None:
        if not isinstance(content, dict) or not content:
            raise ValueError("content doit être un dict JSON-sérialisable non vide")
        try:
            canonical_json_bytes(content)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"content n'est pas JSON-sérialisable : {exc}") from exc

    def _require(self, version_id: str) -> GenomeVersion:
        version = self._repo.get(version_id)
        if version is None:
            raise LookupError(f"version de Genome « {version_id} » introuvable")
        return version

    def _record_event(self, version: GenomeVersion, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.genome",
                msg=kind, context={"genome_version_id": version.id, "kind": kind, **details},
            )

    def _record_audit(self, version: GenomeVersion, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=version.id, details=details,
            )
