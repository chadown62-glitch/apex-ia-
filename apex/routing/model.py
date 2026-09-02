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
from apex.obs.status import DataStatus, Measurement


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
class ModelPerformanceStats:
    """Agrégat PUR sur des exécutions réelles (`apex.agent_registry.model.
    AgentExecution`) pour un `model_id` donné — jamais une statistique
    inventée. Construit par `apex.agent_registry.performance.
    aggregate_model_performance`, jamais ici : ce module ne dépend jamais
    d'`apex.agent_registry` (le sens d'import réel du dépôt est l'inverse
    — `apex.agent_registry` importe déjà `apex.models`/`apex.routing`),
    seulement du type de données que ce module accepte en entrée.

    **Distinction stricte 0 / aucune donnée / unavailable**, exigée par la
    directive « Adaptive Model Router V1 » :
    - `usage_count`/`success_count`/`failure_count` sont des ENTIERS
      ordinaires, jamais un `Measurement` — compter des lignes trouvées est
      toujours un fait réel, y compris `0` (« aucune exécution connue pour
      ce modèle » est une réponse réelle, pas une donnée manquante).
    - `success_rate`/`mean_latency_s` sont des `Measurement` : `REAL`
      SEULEMENT si au moins une exécution exploitable existe (division par
      `usage_count=0` interdite — jamais `0.0` inventé), `UNKNOWN` sinon.
    - `failure_reasons` compte les `FallbackReason` réellement observés
      parmi les échecs — jamais un vocabulaire plus large que ce qui a
      réellement été produit par `apex.agent_registry.fallback.
      classify_outcome`.
    """

    model_id: str
    usage_count: int
    success_count: int
    failure_count: int
    success_rate: Measurement
    mean_latency_s: Measurement
    #: Comptages de tokens agrégés — `REAL` seulement si au moins une
    #: exécution a fourni une mesure réelle (`GenerationResult.
    #: tokens_prompt/tokens_completion` avec `Measurement.status is REAL`),
    #: `UNKNOWN` sinon. Donnée INFORMATIVE, documentée mais délibérément
    #: PAS transformée en contribution de score (`token_efficiency`) dans
    #: cette V1 : une « efficacité » exigerait un dénominateur réel (coût
    #: $ ou qualité), tous deux `unavailable` aujourd'hui (voir §3 de
    #: l'audit) — inventer une formule sans ce dénominateur serait
    #: exactement l'erreur que cette passe interdit.
    mean_tokens_prompt: Measurement = field(default_factory=Measurement.unknown)
    mean_tokens_completion: Measurement = field(default_factory=Measurement.unknown)
    failure_reasons: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreContribution:
    """Une contribution NOMMÉE au score total d'un candidat — jamais un
    nombre nu. `status` distingue une contribution réellement calculée
    (`DataStatus.REAL`) d'un axe explicitement non disponible aujourd'hui
    (`DataStatus.UNKNOWN` — `eval_quality`/`cost` dans cette passe, faute
    de source de données réelle, voir le rapport « Adaptive Model Router
    V1 ») : un axe `UNKNOWN` reste TOUJOURS visible dans la décomposition,
    jamais silencieusement omis, mais contribue `0.0` au total — jamais
    une valeur devinée à sa place."""

    name: str
    value: float
    status: DataStatus
    reason: str


@dataclass(frozen=True)
class ScoreBreakdown:
    """Décomposition auditable et fonctionnelle du score d'un
    `ModelCandidate` — jamais une chaîne de raisonnement libre (aucun
    chain-of-thought). `total` est la somme des `value` de chaque
    `ScoreContribution` dont `status is DataStatus.REAL` uniquement."""

    contributions: tuple[ScoreContribution, ...]

    @property
    def total(self) -> float:
        return sum(c.value for c in self.contributions if c.status is DataStatus.REAL)


@dataclass(frozen=True)
class ScoredCandidate:
    """`reasons` porte TOUJOURS l'explication du score — jamais un nombre
    sans justification lisible (même discipline que `CostGainEstimate.
    reason`). `breakdown` (passe « Adaptive Model Router V1 ») porte la
    même explication sous forme structurée et auditable — `None`
    uniquement pour les candidats déjà exclus par les filtres stricts
    (capacité/contexte/disponibilité/`ExternalPolicy`), qui n'atteignent
    jamais la phase de scoring."""

    candidate: ModelCandidate
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    breakdown: ScoreBreakdown | None = None


@dataclass(frozen=True)
class RoutingDecision:
    """`selected is None` si aucun candidat ne satisfait la requête —
    jamais un choix par défaut deviné. `ranked_candidates` porte TOUJOURS
    la liste complète examinée, retenue ou non, pour audit."""

    selected: ModelCandidate | None
    reason: str
    ranked_candidates: tuple[ScoredCandidate, ...]
