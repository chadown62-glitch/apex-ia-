"""Sorties du knowledge store — deux chemins qui ne se confondent pas :

- `to_training_candidates` : vers l'entraînement du modèle APEX, réservé aux
  éléments ACCEPT, HIGH/MEDIUM_VALUE, licence FREE/COMPATIBLE et provenance
  complète. Découpés en fragments qui gardent tous la source d'origine.
- `to_memory_records` : vers la mémoire (RAG) — utile au raisonnement, mais
  ce n'est PAS de l'entraînement. Toujours `external_provenance=True` et
  jamais VERIFIED : une source externe acceptée reste une affirmation à
  confirmer, pas une vérité."""

from __future__ import annotations

from collections.abc import Iterable

from apex.knowledge.model import Decision, KnowledgeItem, QualityClass, sha256_text
from apex.knowledge.pipeline import chunks_of
from apex.neural.dataset import TrainingCandidate

_TRAINABLE = (QualityClass.HIGH_VALUE, QualityClass.MEDIUM_VALUE)


def exclusion_reason(item: KnowledgeItem) -> str | None:
    if item.decision is not Decision.ACCEPT:
        return f"décision {item.decision.value}"
    if item.quality not in _TRAINABLE:
        return f"qualité {item.quality.value if item.quality else 'absente'}"
    if not item.provenance.license.allows_training:
        return f"licence {item.provenance.license.status.value}"
    if not item.provenance.is_complete():
        return "provenance incomplète"
    if not item.text:
        return "texte non conservé"
    return None


def to_training_candidates(
    items: Iterable[KnowledgeItem], *, chunk_words: int = 220
) -> list[TrainingCandidate]:
    candidates: list[TrainingCandidate] = []
    for item in items:
        if exclusion_reason(item) is not None or item.text is None or item.quality is None:
            continue
        for index, chunk in enumerate(chunks_of(item.text, chunk_words)):
            candidates.append(
                TrainingCandidate(
                    candidate_id=f"{item.item_id}#{index}",
                    text=chunk,
                    source_id=item.provenance.source_id,
                    content_hash=sha256_text(chunk),
                    license_status=item.provenance.license.status.value,
                    quality=item.quality.value,
                    origin=f"knowledge:{item.provenance.kind.value}",
                    evidence=item.provenance.evidence_refs,
                )
            )
    return candidates


def to_memory_records(
    items: Iterable[KnowledgeItem], *, created_by: str = "knowledge-agent"
) -> list[object]:
    from apex.memory.model import MemoryKind, MemoryRecord, VerificationStatus

    records: list[object] = []
    for item in items:
        if item.decision is not Decision.ACCEPT or not item.text:
            continue
        records.append(
            MemoryRecord.new(
                kind=MemoryKind.SEMANTIC,
                content=item.text,
                source=item.provenance.source_id,
                created_by=created_by,
                domain=item.provenance.kind.value,
                confidence=min(item.confidence, 0.7),
                verification_status=VerificationStatus.UNCERTAIN,
                evidence_for=item.provenance.evidence_refs,
                external_provenance=True,
                external_source=item.provenance.publisher or item.provenance.source_id,
            )
        )
    return records
