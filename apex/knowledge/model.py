"""Vocabulaire du Knowledge Acquisition Agent.

L'agent n'est pas le cerveau d'APEX : il TROUVE, VÉRIFIE, NETTOIE et
STRUCTURE des connaissances pour l'entraînement, la mémoire (RAG) et les
tests. Tout contenu externe est une DONNÉE non fiable, jamais une instruction.
Une source prestigieuse est un signal de qualité, jamais une preuve."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

_ITEM_NAMESPACE = uuid.UUID("5b0e8a3c-6f1d-4c1e-9a57-a9e3c0d4b2f1")


def utcnow() -> datetime:
    return datetime.now(UTC)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class Stage(StrEnum):
    """Où en est un contenu : ces états ne se confondent jamais."""

    RAW = "raw_source"
    NORMALIZED = "normalized_source"
    CURATED = "curated_knowledge"
    TRAINING_CANDIDATE = "training_candidate"


class SourceKind(StrEnum):
    BOOK = "book"
    COURSE = "course"
    PAPER = "paper"
    DOCUMENTATION = "documentation"
    STANDARD = "standard"
    TUTORIAL = "tutorial"
    VIDEO = "video"
    IMAGE = "image"
    DATASET = "dataset"
    REPOSITORY = "repository"
    WEB_PAGE = "web_page"
    EXPERIENCE = "experience"
    OTHER = "other"


class LicenseStatus(StrEnum):
    FREE = "free"
    COMPATIBLE = "compatible"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class QualityClass(StrEnum):
    HIGH_VALUE = "high_value"
    MEDIUM_VALUE = "medium_value"
    LOW_VALUE = "low_value"
    DUPLICATE = "duplicate"
    CONTRADICTORY = "contradictory"
    UNSAFE = "unsafe"
    UNVERIFIED = "unverified"
    LICENSE_UNKNOWN = "license_unknown"
    REJECTED = "rejected"


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    QUARANTINE = "quarantine"
    REVIEW = "review"
    PENDING = "pending"


@dataclass(frozen=True)
class LicenseInfo:
    status: LicenseStatus
    identifier: str | None
    evidence: str

    @property
    def allows_full_text(self) -> bool:
        return self.status in (LicenseStatus.FREE, LicenseStatus.COMPATIBLE)

    @property
    def allows_training(self) -> bool:
        return self.allows_full_text


@dataclass(frozen=True)
class SourceRef:
    """Ce que la découverte propose — rien n'est encore acquis."""

    locator: str
    kind: SourceKind
    title: str | None = None
    author: str | None = None
    date: str | None = None
    publisher: str | None = None
    license_hint: str | None = None
    discovered_by: str = "unknown"


@dataclass(frozen=True)
class Transformation:
    stage: str
    detail: str
    at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class Provenance:
    source_id: str
    kind: SourceKind
    title: str | None
    author: str | None
    date: str | None
    publisher: str | None
    license: LicenseInfo
    acquired_at: datetime
    source_hash: str | None
    content_hash: str | None
    extraction_method: str | None
    transformations: tuple[Transformation, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def is_complete(self) -> bool:
        return bool(self.source_id and self.content_hash and self.extraction_method and self.acquired_at)

    def with_step(self, stage: str, detail: str) -> Provenance:
        return replace(self, transformations=(*self.transformations, Transformation(stage, detail)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "publisher": self.publisher,
            "license": {
                "status": self.license.status.value,
                "identifier": self.license.identifier,
                "evidence": self.license.evidence,
            },
            "acquired_at": self.acquired_at.isoformat(),
            "source_hash": self.source_hash,
            "content_hash": self.content_hash,
            "extraction_method": self.extraction_method,
            "transformations": [
                {"stage": t.stage, "detail": t.detail, "at": t.at.isoformat()} for t in self.transformations
            ],
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class StageRecord:
    """Trace observable d'une étape : ce qu'elle a vu et ce qu'elle a décidé."""

    stage: str
    outcome: str
    detail: str
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    stage: Stage
    text: str | None
    provenance: Provenance
    language: str | None = None
    fmt: str | None = None
    quality: QualityClass | None = None
    decision: Decision = Decision.PENDING
    confidence: float = 0.0
    flags: tuple[str, ...] = ()
    history: tuple[StageRecord, ...] = ()
    sections: tuple[str, ...] = ()

    @classmethod
    def new(cls, text: str | None, provenance: Provenance) -> KnowledgeItem:
        """Identifiant adressé par le contenu : même source et mêmes octets ->
        même identifiant d'une exécution à l'autre (benchmarks comparables)."""
        key = f"{provenance.source_id}|{provenance.source_hash or 'unacquired'}"
        item_id = str(uuid.uuid5(_ITEM_NAMESPACE, key))
        return cls(item_id=item_id, stage=Stage.RAW, text=text, provenance=provenance)

    def record(self, stage: str, outcome: str, detail: str, **metrics: float) -> KnowledgeItem:
        return replace(self, history=(*self.history, StageRecord(stage, outcome, detail, dict(metrics))))

    def flagged(self, flag: str) -> KnowledgeItem:
        return self if flag in self.flags else replace(self, flags=(*self.flags, flag))

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "stage": self.stage.value,
            "text": self.text,
            "provenance": self.provenance.to_dict(),
            "language": self.language,
            "format": self.fmt,
            "quality": self.quality.value if self.quality else None,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "flags": list(self.flags),
            "history": [
                {"stage": r.stage, "outcome": r.outcome, "detail": r.detail, "metrics": r.metrics}
                for r in self.history
            ],
            "sections": list(self.sections),
        }


def provenance_from_dict(data: dict[str, Any]) -> Provenance:
    license_data = data["license"]
    return Provenance(
        source_id=data["source_id"],
        kind=SourceKind(data["kind"]),
        title=data.get("title"),
        author=data.get("author"),
        date=data.get("date"),
        publisher=data.get("publisher"),
        license=LicenseInfo(
            LicenseStatus(license_data["status"]),
            license_data.get("identifier"),
            license_data.get("evidence", ""),
        ),
        acquired_at=datetime.fromisoformat(data["acquired_at"]),
        source_hash=data.get("source_hash"),
        content_hash=data.get("content_hash"),
        extraction_method=data.get("extraction_method"),
        transformations=tuple(
            Transformation(t["stage"], t["detail"], datetime.fromisoformat(t["at"]))
            for t in data.get("transformations", [])
        ),
        evidence_refs=tuple(data.get("evidence_refs", [])),
    )


def item_from_dict(data: dict[str, Any]) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=data["item_id"],
        stage=Stage(data["stage"]),
        text=data.get("text"),
        provenance=provenance_from_dict(data["provenance"]),
        language=data.get("language"),
        fmt=data.get("format"),
        quality=QualityClass(data["quality"]) if data.get("quality") else None,
        decision=Decision(data["decision"]),
        confidence=float(data.get("confidence", 0.0)),
        flags=tuple(data.get("flags", [])),
        history=tuple(
            StageRecord(r["stage"], r["outcome"], r["detail"], dict(r.get("metrics", {})))
            for r in data.get("history", [])
        ),
        sections=tuple(data.get("sections", [])),
    )
