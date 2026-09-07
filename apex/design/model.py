"""Modèle de domaine du Design Engine — dataclasses immuables, traçabilité
complète (chaque étape produit un `StepRecord` ; chaque résultat porte sa
`provenance`, jamais une valeur plausible non sourcée).

Un `DesignPackage` référence tout le reste (requête, brief, recherche,
concepts, prompts visuels, résultats visuels, validation, artefacts
persistés) — c'est l'unité de résultat retournée par l'Operator
(`GetTaskResult`-équivalent pour une conception) et l'unité sérialisée vers
`apex.artifacts` pour une persistance durable et adressée par contenu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apex.design.states import DesignPipelineState, DesignStep, DesignStepState
from apex.policy.model import Subject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class DesignRequest:
    """La demande initiale en langage naturel — jamais modifiée après
    création, seule source de vérité de « ce qui a été demandé »."""

    id: str
    text: str
    requested_by: Subject
    created_at: datetime

    @classmethod
    def new(cls, text: str, *, requested_by: Subject) -> "DesignRequest":
        return cls(id=new_id("design"), text=text, requested_by=requested_by, created_at=_utcnow())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "requested_by": self.requested_by.value,
            "created_at": _iso(self.created_at),
        }


@dataclass(frozen=True)
class StepRecord:
    """Trace d'exécution d'UNE étape — état, entrée/sortie résumées,
    erreur éventuelle, logs, durée, provenance. Jamais purgé : l'historique
    complet (y compris les étapes en échec) reste dans `DesignPackage.steps`
    pour permettre une reprise (`resume`) et un audit a posteriori fidèles."""

    step: DesignStep
    state: DesignStepState
    input_summary: str
    output_summary: str
    provenance: str
    logs: tuple[str, ...] = ()
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "step": self.step.value,
            "state": self.state.value,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "provenance": self.provenance,
            "logs": list(self.logs),
            "error": self.error,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class DesignBrief:
    """Brief structuré dérivé de la requête (et, si disponible, de la
    recherche) — jamais fabriqué : une extraction déterministe et
    documentée du texte fourni (`provenance="deterministic-template"`), ou
    une synthèse via `ModelProvider` réellement invoqué si configuré
    (`provenance="model:<endpoint>"`)."""

    design_id: str
    summary: str
    goals: tuple[str, ...]
    constraints: tuple[str, ...]
    audience: str
    style_keywords: tuple[str, ...]
    source_request: str
    provenance: str

    def to_dict(self) -> dict:
        return {
            "design_id": self.design_id,
            "summary": self.summary,
            "goals": list(self.goals),
            "constraints": list(self.constraints),
            "audience": self.audience,
            "style_keywords": list(self.style_keywords),
            "source_request": self.source_request,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ResearchResult:
    """Un résultat de recherche structuré — jamais synthétisé : chaque
    instance provient réellement d'un `ResearchProvider.search()` appelé,
    `provenance` porte le nom du backend qui l'a produit (ou l'absence)."""

    source: str
    title: str
    url: str | None
    excerpt: str
    relevance: float
    timestamp: datetime
    provenance: str

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "excerpt": self.excerpt,
            "relevance": self.relevance,
            "timestamp": _iso(self.timestamp),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class DesignConcept:
    """Une piste de conception versionnée — `version` distingue plusieurs
    concepts issus du même brief (un par mot-clé de style dominant, au
    minimum un). `based_on_research` référence les `ResearchResult` (par
    index dans `DesignPackage.research`) réellement utilisés, jamais une
    liste vide prétendant une base qui n'existe pas si la recherche a
    produit zéro résultat."""

    id: str
    design_id: str
    version: int
    title: str
    narrative: str
    key_elements: tuple[str, ...]
    based_on_research: tuple[int, ...]
    provenance: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "design_id": self.design_id,
            "version": self.version,
            "title": self.title,
            "narrative": self.narrative,
            "key_elements": list(self.key_elements),
            "based_on_research": list(self.based_on_research),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class VisualPrompt:
    """Prompt visuel structuré — les 10 dimensions demandées par la
    mission, plus le concept d'origine et le numéro de variante. `render()`
    produit le texte à plat consommé par `VisualGenerationProvider`."""

    id: str
    concept_id: str
    variant: int
    subject: str
    environment: str
    composition: str
    architecture_design: str
    materials: str
    colors: str
    light: str
    camera: str
    style: str
    constraints: str
    elements_to_avoid: str

    def render(self) -> str:
        parts = [
            f"Sujet : {self.subject}",
            f"Environnement : {self.environment}",
            f"Composition : {self.composition}",
            f"Conception architecturale : {self.architecture_design}",
            f"Matériaux : {self.materials}",
            f"Couleurs : {self.colors}",
            f"Lumière : {self.light}",
            f"Caméra : {self.camera}",
            f"Style : {self.style}",
            f"Contraintes : {self.constraints}",
            f"À éviter : {self.elements_to_avoid}",
        ]
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "concept_id": self.concept_id,
            "variant": self.variant,
            "subject": self.subject,
            "environment": self.environment,
            "composition": self.composition,
            "architecture_design": self.architecture_design,
            "materials": self.materials,
            "colors": self.colors,
            "light": self.light,
            "camera": self.camera,
            "style": self.style,
            "constraints": self.constraints,
            "elements_to_avoid": self.elements_to_avoid,
            "rendered": self.render(),
        }


@dataclass(frozen=True)
class VisualResult:
    """Résultat d'une tentative de génération visuelle — `state` distingue
    honnêtement un succès réel (`"completed"`, avec `artifact_content_hash`
    non nul, un octet réellement stocké) d'une indisponibilité de backend
    (`"unavailable"`, jamais accompagnée d'un `artifact_content_hash`) et
    d'un échec (`"failed"`, backend présent mais l'appel a échoué)."""

    id: str
    prompt_id: str
    state: str  # "completed" | "unavailable" | "failed"
    provider: str
    detail: str
    artifact_content_hash: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt_id": self.prompt_id,
            "state": self.state,
            "provider": self.provider,
            "detail": self.detail,
            "artifact_content_hash": self.artifact_content_hash,
        }


@dataclass(frozen=True)
class DesignArtifactRef:
    """Référence vers un artefact réellement écrit dans `apex.artifacts`
    (adressé par contenu, `sha256:...`) — jamais un identifiant inventé."""

    content_hash: str
    kind: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {"content_hash": self.content_hash, "kind": self.kind, "created_at": _iso(self.created_at)}


@dataclass(frozen=True)
class DesignValidation:
    """Résultat de la validation structurelle (`apex.design.validation`) —
    `checks` liste CHAQUE vérification réellement exécutée (passée ou non),
    `failures` seulement celles qui ont échoué. Jamais une validation
    déclarée `passed=True` sans que `checks` ne soit non vide : une absence
    de vérification n'est jamais une réussite silencieuse."""

    design_id: str
    passed: bool
    checks: tuple[str, ...]
    failures: tuple[str, ...]
    validated_at: datetime

    def to_dict(self) -> dict:
        return {
            "design_id": self.design_id,
            "passed": self.passed,
            "checks": list(self.checks),
            "failures": list(self.failures),
            "validated_at": _iso(self.validated_at),
        }


@dataclass(frozen=True)
class DesignPackage:
    """Résultat complet d'une exécution du pipeline — l'unité retournée à
    l'appelant (Operator/API) et sérialisée vers `apex.artifacts`. Immuable :
    chaque étape produit un NOUVEAU `DesignPackage` (jamais une mutation en
    place), ce qui rend `resume()` capable de repartir d'un état antérieur
    fidèle sans risque de corruption partagée."""

    design_id: str
    request: DesignRequest
    state: DesignPipelineState
    steps: tuple[StepRecord, ...] = ()
    brief: DesignBrief | None = None
    research: tuple[ResearchResult, ...] = ()
    concepts: tuple[DesignConcept, ...] = ()
    visual_prompts: tuple[VisualPrompt, ...] = ()
    visual_results: tuple[VisualResult, ...] = ()
    validation: DesignValidation | None = None
    artifacts: tuple[DesignArtifactRef, ...] = ()
    error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None

    @property
    def ready(self) -> bool:
        return self.state.is_terminal

    def to_dict(self) -> dict:
        return {
            "design_id": self.design_id,
            "request": self.request.to_dict(),
            "state": self.state.value,
            "steps": [s.to_dict() for s in self.steps],
            "brief": self.brief.to_dict() if self.brief else None,
            "research": [r.to_dict() for r in self.research],
            "concepts": [c.to_dict() for c in self.concepts],
            "visual_prompts": [p.to_dict() for p in self.visual_prompts],
            "visual_results": [v.to_dict() for v in self.visual_results],
            "validation": self.validation.to_dict() if self.validation else None,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error,
            "created_at": _iso(self.created_at),
            "completed_at": _iso(self.completed_at),
        }
