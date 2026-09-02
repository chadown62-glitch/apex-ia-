"""Vocabulaire de `apex.routing` — `Capability` (fermé), `RoutingRequest`/
`RoutingDecision`, dérivation de capacités à partir d'un `ModelCandidate`
déjà réel (`apex.models.registry`).

**Discipline stricte sur ce qui est dérivé** : une capacité n'est jamais
affirmée sans un signal réel dans le schéma déjà validé.
`CODING`/`GENERAL`/`JUDGE`/`FAST`/`EMBEDDING` viennent de `ModelRef.
capability_class` (déjà réel, phase 1). `LOCAL` vient de `ModelCandidate.
is_local` (déjà réel). `LONG_CONTEXT` vient de `ModelEndpoint.max_context`
(déjà réel). `TEXT` est dérivée pour TOUT candidat réel — chaque backend de
ce dépôt (`OllamaProvider`/`OpenAICompatibleProvider`) est une API de
génération de texte, une affirmation vérifiée par construction, jamais
devinée. `REASONING`/`VISION`/`TOOL_USE`/`AUDIO`/`VIDEO`/`SCREEN`/`VOICE`
restent dans le vocabulaire fermé (prêts pour de futurs `ModelEndpoint`
multimodaux réels — passe « Model/Provider Catalog », §9) mais ne sont
JAMAIS dérivées aujourd'hui — aucun champ du schéma actuel n'en porte la
preuve (`ModelRef`/`ModelEndpoint` ne déclarent aucune modalité), et
deviner serait exactement l'erreur que ce dépôt refuse partout ailleurs
(Gate, `ModelCapabilityReport`, `SandboxResult`). Le jour où un modèle
multimodal réel est configuré, ces valeurs se dérivent d'un champ réel à
ajouter alors — jamais du seul nom du `runtime`/fournisseur (un fournisseur
qui *permet* la vision ne prouve pas que CE modèle la supporte).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from apex.models.registry import ModelCandidate


class Capability(str, Enum):
    """Vocabulaire fermé — un modèle peut en porter plusieurs à la fois."""

    TEXT = "text"
    CODING = "coding"
    REASONING = "reasoning"
    GENERAL = "general"
    JUDGE = "judge"
    VISION = "vision"
    AUDIO = "audio"
    VOICE = "voice"
    VIDEO = "video"
    SCREEN = "screen"
    EMBEDDING = "embedding"
    FAST = "fast"
    LONG_CONTEXT = "long_context"
    TOOL_USE = "tool_use"
    LOCAL = "local"


#: `ModelRef.capability_class` (déjà réel) -> `Capability`. Ensemble fermé,
#: identique à celui déjà validé par `apex.config.schema`
#: (`coder`/`generalist`/`judge`/`fast`/`embed`) — aucune valeur nouvelle
#: inventée ici.
_CAPABILITY_CLASS_MAP: dict[str, Capability] = {
    "coder": Capability.CODING,
    "generalist": Capability.GENERAL,
    "judge": Capability.JUDGE,
    "fast": Capability.FAST,
    "embed": Capability.EMBEDDING,
}

#: Seuil arbitraire mais documenté (même discipline que
#: `apex.weakness.detector.MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS`) — à ajuster
#: seulement sur preuve, jamais en fonction d'un résultat souhaité.
LONG_CONTEXT_THRESHOLD_TOKENS = 32_768


def derive_capabilities(candidate: ModelCandidate) -> frozenset[Capability]:
    caps: set[Capability] = {Capability.TEXT}
    mapped = _CAPABILITY_CLASS_MAP.get(candidate.ref.capability_class)
    if mapped is not None:
        caps.add(mapped)
    if candidate.is_local:
        caps.add(Capability.LOCAL)
    if candidate.endpoint is not None and candidate.endpoint.max_context >= LONG_CONTEXT_THRESHOLD_TOKENS:
        caps.add(Capability.LONG_CONTEXT)
    return frozenset(caps)


@dataclass(frozen=True)
class RoutingRequest:
    """Ce qu'un appelant demande — jamais un modèle nommé en dur par
    l'appelant (principe déjà posé pour `ModelEndpoint`/`ModelRef`,
    étendu ici)."""

    required_capabilities: frozenset[Capability] = frozenset()
    minimum_context: int | None = None
    prefer_local: bool = True
    allow_external: bool = False
    max_candidates: int = 3


@dataclass(frozen=True)
class ScoredCandidate:
    """`reasons` porte TOUJOURS l'explication du score — jamais un nombre
    sans justification lisible (même discipline que `CostGainEstimate.
    reason`)."""

    candidate: ModelCandidate
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RoutingDecision:
    """`selected is None` si aucun candidat ne satisfait la requête —
    jamais un choix par défaut deviné. `ranked_candidates` porte TOUJOURS
    la liste complète examinée, retenue ou non, pour audit."""

    selected: ModelCandidate | None
    reason: str
    ranked_candidates: tuple[ScoredCandidate, ...]
