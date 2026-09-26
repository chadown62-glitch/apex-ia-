"""Knowledge Acquisition Agent — pipeline observable, étape par étape.

    discovery -> acquisition -> license -> extraction -> normalization
    -> language -> relevance -> quality -> evidence -> dedup -> contradiction
    -> injection -> structuring -> provenance -> curation -> store

Chaque étape ajoute un `StageRecord` à l'historique de l'élément : ce qu'elle a
mesuré et ce qu'elle a conclu. La curation est la SEULE à décider, par une
règle explicite (voir `curate`). Le texte d'une source n'est jamais interprété
comme une consigne : il est mesuré, jamais exécuté ; un texte qui tente de
donner des ordres est mis en QUARANTINE, et rien dans le pipeline ne lit le
texte pour choisir une décision autrement que par ces mesures."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from apex.knowledge.acquisition import Acquirer, AcquisitionResult, acquire
from apex.knowledge.extraction import extract
from apex.knowledge.filters import (
    ClaimIndex,
    DuplicateIndex,
    detect_language,
    injection_matches,
    normalize,
    quality,
    relevance,
)
from apex.knowledge.licensing import classify_license
from apex.knowledge.model import (
    Decision,
    KnowledgeItem,
    LicenseStatus,
    Provenance,
    QualityClass,
    SourceRef,
    Stage,
    sha256_text,
    utcnow,
)
from apex.knowledge.multimodal import StepStatus, analyse_image

STAGES: tuple[str, ...] = (
    "discovery",
    "acquisition",
    "license",
    "extraction",
    "normalization",
    "language",
    "relevance",
    "quality",
    "evidence",
    "dedup",
    "contradiction",
    "injection",
    "structuring",
    "provenance",
    "curation",
)

# Signaux posés par les étapes ; `curate` les traduit en décision.
FLAG_ACCESS_REFUSED = "access_refused"
FLAG_NO_TEXT = "no_extractable_text"
FLAG_LICENSE_RESTRICTED = "license_restricted"
FLAG_LICENSE_UNKNOWN = "license_unknown"
FLAG_LANGUAGE = "language_not_allowed"
FLAG_IRRELEVANT = "irrelevant"
FLAG_LOW_QUALITY = "low_quality"
FLAG_UNVERIFIED = "unverified"
FLAG_DUPLICATE = "duplicate"
FLAG_CONTRADICTION = "contradiction"
FLAG_INJECTION = "prompt_injection"
FLAG_PROVENANCE = "provenance_incomplete"


@dataclass(frozen=True)
class AgentConfig:
    topics: tuple[str, ...] = ()
    min_relevance: float = 0.5
    languages: tuple[str, ...] = ("en", "fr")
    reject_below: float = 0.5
    high_value_from: float = 0.8
    duplicate_threshold: float = 0.8
    chunk_words: int = 220


@dataclass(frozen=True)
class AgentReport:
    items: tuple[KnowledgeItem, ...]
    decisions: dict[str, int]
    qualities: dict[str, int]
    stage_outcomes: dict[str, dict[str, int]] = field(default_factory=dict)

    def accepted(self) -> tuple[KnowledgeItem, ...]:
        return tuple(i for i in self.items if i.decision is Decision.ACCEPT)


def curate(item: KnowledgeItem, score: float, config: AgentConfig) -> tuple[Decision, QualityClass, str]:
    """Règle de décision — ordre de priorité explicite :
    1. injection -> QUARANTINE / UNSAFE (même si le reste est parfait) ;
    2. accès refusé, licence restrictive, pas de texte, provenance incomplète
       -> REJECT ; doublon -> REJECT / DUPLICATE ; hors sujet, langue ou
       qualité insuffisante -> REJECT / LOW_VALUE ;
    3. licence inconnue -> REVIEW / LICENSE_UNKNOWN ; contradiction ->
       REVIEW / CONTRADICTORY ; preuve d'intégrité absente -> REVIEW / UNVERIFIED ;
    4. sinon ACCEPT, HIGH_VALUE ou MEDIUM_VALUE selon le score de qualité."""
    flags = set(item.flags)
    if FLAG_INJECTION in flags:
        return Decision.QUARANTINE, QualityClass.UNSAFE, "tentative d'instruction dans le contenu"
    for flag, reason in (
        (FLAG_ACCESS_REFUSED, "accès refusé ou impossible"),
        (FLAG_LICENSE_RESTRICTED, "licence restrictive"),
        (FLAG_NO_TEXT, "aucun texte exploitable"),
        (FLAG_PROVENANCE, "provenance incomplète"),
    ):
        if flag in flags:
            return Decision.REJECT, QualityClass.REJECTED, reason
    if FLAG_DUPLICATE in flags:
        return Decision.REJECT, QualityClass.DUPLICATE, "doublon d'un élément déjà accepté"
    for flag, reason in (
        (FLAG_IRRELEVANT, "hors sujet"),
        (FLAG_LANGUAGE, "langue non retenue"),
        (FLAG_LOW_QUALITY, "qualité insuffisante"),
    ):
        if flag in flags:
            return Decision.REJECT, QualityClass.LOW_VALUE, reason
    if FLAG_LICENSE_UNKNOWN in flags:
        return Decision.REVIEW, QualityClass.LICENSE_UNKNOWN, "licence inconnue : métadonnées seulement"
    if FLAG_CONTRADICTION in flags:
        return Decision.REVIEW, QualityClass.CONTRADICTORY, "contredit un élément déjà accepté"
    if FLAG_UNVERIFIED in flags:
        return Decision.REVIEW, QualityClass.UNVERIFIED, "intégrité de la source non établie"
    klass = QualityClass.HIGH_VALUE if score >= config.high_value_from else QualityClass.MEDIUM_VALUE
    return Decision.ACCEPT, klass, "tous les contrôles passés"


_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def sections_of(text: str) -> tuple[str, ...]:
    return tuple(m.group(1).strip() for m in _HEADING.finditer(text))


def chunks_of(text: str, max_words: int) -> tuple[str, ...]:
    """Découpe par paragraphes, sans jamais couper un paragraphe en deux sauf
    s'il dépasse seul la limite."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        n = len(paragraph.split())
        if current and size + n > max_words:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        if n > max_words:
            tokens = paragraph.split()
            chunks.extend(" ".join(tokens[i : i + max_words]) for i in range(0, n, max_words))
            continue
        current.append(paragraph)
        size += n
    if current:
        chunks.append("\n\n".join(current))
    return tuple(chunks)


class KnowledgeAgent:
    def __init__(self, acquirers: Sequence[Acquirer], config: AgentConfig | None = None) -> None:
        self.acquirers = tuple(acquirers)
        self.config = config or AgentConfig()
        self.duplicates = DuplicateIndex(self.config.duplicate_threshold)
        self.claims = ClaimIndex()

    def process(self, ref: SourceRef) -> KnowledgeItem:
        cfg = self.config
        acquired = acquire(ref, self.acquirers)
        license_info = classify_license(ref.license_hint)
        provenance = Provenance(
            source_id=ref.locator,
            kind=ref.kind,
            title=ref.title,
            author=ref.author,
            date=ref.date,
            publisher=ref.publisher,
            license=license_info,
            acquired_at=utcnow(),
            source_hash=acquired.source_hash,
            content_hash=None,
            extraction_method=None,
        ).with_step("discovery", ref.discovered_by)
        item = KnowledgeItem.new(None, provenance).record(
            "discovery", "found", f"{ref.kind.value} via {ref.discovered_by}"
        )
        item = self._acquisition(item, acquired)
        if FLAG_ACCESS_REFUSED in item.flags:
            return self._finish(item, 0.0)

        item = self._license(item)
        text = self._extract(item, acquired)
        item = text[0]
        if text[1] is None:
            return self._finish(item, 0.0)
        body = text[1]

        normalized = normalize(body)
        item = replace(
            item,
            stage=Stage.NORMALIZED,
            provenance=replace(
                item.provenance.with_step("normalization", "NFC, espaces, lignes vides"),
                content_hash=sha256_text(normalized),
            ),
        )
        item = item.record("normalization", "done", "NFC + espaces", chars=len(normalized))

        language, lang_score = detect_language(normalized)
        item = replace(item, language=language)
        lang_ok = language in cfg.languages
        item = item.record("language", "ok" if lang_ok else "flag", language, stopword_ratio=lang_score)
        if not lang_ok:
            item = item.flagged(FLAG_LANGUAGE)

        rel = relevance(normalized, cfg.topics)
        rel_ok = rel >= cfg.min_relevance
        item = item.record("relevance", "ok" if rel_ok else "flag", f"thèmes {cfg.topics}", relevance=rel)
        if not rel_ok:
            item = item.flagged(FLAG_IRRELEVANT)

        q = quality(normalized)
        q_ok = q.score >= cfg.reject_below
        item = item.record(
            "quality",
            "ok" if q_ok else "flag",
            "; ".join(q.reasons) or "aucune pénalité",
            score=q.score,
            words=q.n_words,
            alpha_ratio=q.alpha_ratio,
            unique_line_ratio=q.unique_line_ratio,
        )
        if not q_ok:
            item = item.flagged(FLAG_LOW_QUALITY)

        item = self._evidence(item, acquired)

        original, sim = self.duplicates.find(normalized)
        item = item.record(
            "dedup",
            "flag" if original else "ok",
            f"doublon de {original}" if original else "nouveau",
            similarity=sim,
        )
        if original:
            item = item.flagged(FLAG_DUPLICATE)

        conflicts = self.claims.conflicts(normalized)
        detail = "; ".join(
            f"{a.subject}: « {a.value} » vs « {b.value} » ({other})" for other, a, b in conflicts[:3]
        )
        item = item.record(
            "contradiction", "flag" if conflicts else "ok", detail or "aucune", conflicts=len(conflicts)
        )
        if conflicts:
            item = item.flagged(FLAG_CONTRADICTION)

        matches = injection_matches(normalized)
        item = item.record(
            "injection",
            "flag" if matches else "ok",
            f"motifs : {list(matches)}" if matches else "aucun motif",
            matches=len(matches),
        )
        if matches:
            item = item.flagged(FLAG_INJECTION)

        item = replace(
            item,
            text=normalized if item.provenance.license.allows_full_text else None,
            sections=sections_of(normalized),
        )
        item = item.record(
            "structuring",
            "done",
            f"{len(item.sections)} section(s)",
            chunks=len(chunks_of(normalized, cfg.chunk_words)),
        )
        if item.text is None:
            item = item.record("license", "metadata_only", "texte intégral non conservé (licence)")
        return self._finish(item, q.score, normalized)

    def run(self, refs: Iterable[SourceRef]) -> AgentReport:
        items = tuple(self.process(ref) for ref in refs)
        outcomes: dict[str, Counter[str]] = {}
        for item in items:
            for record in item.history:
                outcomes.setdefault(record.stage, Counter())[record.outcome] += 1
        return AgentReport(
            items=items,
            decisions=dict(Counter(i.decision.value for i in items)),
            qualities=dict(Counter(i.quality.value for i in items if i.quality)),
            stage_outcomes={stage: dict(c) for stage, c in outcomes.items()},
        )

    # Étapes détaillées -------------------------------------------------

    def _acquisition(self, item: KnowledgeItem, acquired: AcquisitionResult) -> KnowledgeItem:
        if not acquired.ok:
            return item.record("acquisition", "refused", acquired.detail).flagged(FLAG_ACCESS_REFUSED)
        return replace(item, provenance=item.provenance.with_step("acquisition", acquired.detail)).record(
            "acquisition", "ok", acquired.detail, bytes=float(len(acquired.data or b""))
        )

    def _license(self, item: KnowledgeItem) -> KnowledgeItem:
        info = item.provenance.license
        item = item.record("license", info.status.value, info.evidence)
        if info.status is LicenseStatus.RESTRICTED:
            return item.flagged(FLAG_LICENSE_RESTRICTED)
        if info.status is LicenseStatus.UNKNOWN:
            return item.flagged(FLAG_LICENSE_UNKNOWN)
        return item

    def _extract(
        self, item: KnowledgeItem, acquired: AcquisitionResult
    ) -> tuple[KnowledgeItem, str | None]:
        content_type = acquired.content_type or ""
        if content_type.startswith("image/") and acquired.data is not None:
            steps = analyse_image(acquired.data)
            for step in steps:
                item = item.record(f"image:{step.name}", step.status.value, step.detail)
            done = [s for s in steps if s.status is StepStatus.DONE]
            item = replace(item, fmt=content_type)
            return item.record(
                "extraction", "no_text", f"image : {len(done)} étape(s) réalisée(s), OCR indisponible"
            ).flagged(FLAG_NO_TEXT), None
        extraction = extract(acquired.data or b"", acquired.content_type)
        item = replace(
            item,
            fmt=extraction.fmt,
            provenance=replace(
                item.provenance.with_step("extraction", extraction.detail),
                extraction_method=extraction.method,
            ),
        )
        if extraction.text is None or not extraction.text.strip():
            return item.record("extraction", "no_text", extraction.detail).flagged(FLAG_NO_TEXT), None
        return item.record(
            "extraction", "ok", extraction.detail, chars=len(extraction.text)
        ), extraction.text

    def _evidence(self, item: KnowledgeItem, acquired: AcquisitionResult) -> KnowledgeItem:
        """Preuve d'intégrité : l'empreinte enregistrée correspond aux octets
        acquis. Une source prestigieuse n'est jamais une preuve en soi : on ne
        vérifie ici que ce qui est vérifiable."""
        data = acquired.data or b""
        recomputed = "sha256:" + hashlib.sha256(data).hexdigest()
        consistent = bool(item.provenance.source_hash) and recomputed == item.provenance.source_hash
        refs = (item.provenance.source_id, recomputed) if consistent else ()
        item = replace(item, provenance=replace(item.provenance, evidence_refs=refs))
        item = item.record(
            "evidence",
            "ok" if consistent else "flag",
            "empreinte de la source vérifiée" if consistent else "empreinte absente ou incohérente",
        )
        return item if consistent else item.flagged(FLAG_UNVERIFIED)

    def _finish(self, item: KnowledgeItem, score: float, normalized: str | None = None) -> KnowledgeItem:
        complete = item.provenance.is_complete()
        item = item.record(
            "provenance",
            "ok" if complete else "flag",
            "complète" if complete else "empreinte ou méthode d'extraction absente",
        )
        if not complete and not ({FLAG_ACCESS_REFUSED, FLAG_NO_TEXT} & set(item.flags)):
            item = item.flagged(FLAG_PROVENANCE)
        decision, klass, reason = curate(item, score, self.config)
        stage = Stage.CURATED if decision is Decision.ACCEPT else item.stage
        confidence = round(score, 3) if decision is Decision.ACCEPT else 0.0
        item = replace(
            item,
            decision=decision,
            quality=klass,
            stage=stage,
            confidence=confidence,
            provenance=item.provenance.with_step("curation", f"{decision.value} : {reason}"),
        )
        item = item.record("curation", decision.value, reason, score=score)
        if decision is Decision.ACCEPT and normalized is not None:
            # Seul un élément ACCEPTÉ nourrit les index : un contenu rejeté ou
            # en quarantaine ne peut ni masquer un doublon ni fabriquer une
            # contradiction contre une source saine.
            self.duplicates.add(item.item_id, normalized)
            self.claims.add(item.item_id, normalized)
        return item
