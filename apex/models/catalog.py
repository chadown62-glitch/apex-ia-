"""`apex.models.catalog` — la source de vérité STRUCTURÉE des providers et
modèles réellement déclarés, dérivée d'`ApexConfig` (déjà la config réelle,
`apex.models.registry.resolve_candidates`).

**PAS une nouvelle registry parallèle** : `ProviderInfo`/`CatalogModel` sont
des vues dérivées, jamais stockées, jamais une seconde source de vérité —
même patron qu'`apex.models.registry.ModelCandidate`, dont ce module est
directement l'extension (capacités, disponibilité, découverte, historique
de performance — vide tant qu'aucune vraie exécution ne l'alimente).
Recalculer le catalogue à partir d'une config modifiée donne toujours le
même résultat ; rien ici ne peut diverger de `config.yaml`/`ApexConfig`
puisque rien n'est dupliqué, seulement composé.

**`Availability`** (état d'usage) — fermé, jamais `UNKNOWN` promu à
`AVAILABLE` sans preuve :
- `DISABLED` — `ModelEndpoint.enabled=False`, jamais sondé.
- `UNKNOWN` — déclaré (candidat avec ou sans endpoint), jamais encore sondé
  en direct (`discover_live()` non appelé, ou candidat sans `ModelEndpoint`
  du tout).
- `AVAILABLE`/`UNAVAILABLE` — résultat RÉEL d'un appel `ModelProvider.
  detect()` via `discover_live()`, jamais autrement.

**`DiscoverySource`** (provenance de la connaissance qu'un modèle existe) —
fermé :
- `CONFIGURED` — présent dans `ApexConfig`, rien de plus.
- `DISCOVERED` — un `detect()` réel a confirmé le endpoint joignable et le
  modèle listé (`GET /api/tags` ou `/v1/models`, réel).
- `VERIFIED` — reste `PLANNED` : aucune génération de Mission ne persiste
  aujourd'hui QUEL `ModelEndpoint` l'a réellement servie
  (`generate_mission_response`/`_via_router` ne stockent que le texte
  résultat, `Mission.result`) — la preuve d'usage réel n'existe donc nulle
  part à interroger. Câbler `VERIFIED` exigerait d'abord cette
  traçabilité, un vrai changement de périmètre pour `Mission`, hors de
  cette passe (voir `docs/DEVELOPMENT_HANDOFF.md`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from apex.config.schema import ApexConfig
from apex.errors import ModelProviderError, TimeoutExceeded
from apex.models.provider import ModelProvider, get_default_provider
from apex.models.registry import ModelCandidate, resolve_candidates
from apex.obs.status import Measurement
from apex.routing.model import Capability, ModelPerformanceStats, derive_capabilities


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


class DiscoverySource(str, Enum):
    CONFIGURED = "configured"
    DISCOVERED = "discovered"
    VERIFIED = "verified"


@dataclass(frozen=True)
class PerformanceHistory:
    """Vide par défaut — JAMAIS une statistique inventée. `apex.selfmodel`/
    `apex.costgain.CostGainEstimator` restent la source réelle d'agrégats
    (par spécialité, pas par modèle à ce jour) ; ce champ n'est PAS une
    duplication de ces composants, seulement l'emplacement prêt pour le
    jour où une agrégation par modèle sera câblée — vide jusque-là."""

    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    estimated_cost: Measurement = field(default_factory=Measurement.unknown)
    observed_latency_s: Measurement = field(default_factory=Measurement.unknown)
    #: Passe « Routing & Execution Observability Hardening » (N+18) —
    #: `apex.routing.model.ModelPerformanceStats.last_failure_reason`/
    #: `.failure_reasons` projetés tels quels, jamais recalculés ici.
    last_failure_reason: str | None = None
    failure_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.usage_count == 0


@dataclass(frozen=True)
class ProviderInfo:
    """Un provider = un (`runtime`, `url`, `secret_key`) distinct, dérivé
    des `ModelEndpoint` déclarés — jamais une entité stockée séparément.
    `id` est déterministe (`f"{runtime}:{url}"`), stable tant que la
    config ne change pas."""

    id: str
    display_name: str
    runtime: str
    base_url: str
    secret_key: str | None
    local: bool
    enabled: bool
    model_ids: tuple[str, ...]

    # `local` répond à une question de PROVIDER (cette connexion sort-elle du
    # poste local ?), dérivée de `runtime == "ollama"` — le même signal que
    # `apex.models.provider.get_default_provider` utilise déjà pour choisir
    # `OllamaProvider` vs `OpenAICompatibleProvider`, jamais une nouvelle
    # heuristique. C'est délibérément DISTINCT de `ModelCandidate.is_local`
    # (question de MODÈLE : `ref.quantization != "external"`) — un provider
    # peut être « local » sans qu'aucun modèle qu'il sert soit encore
    # résolu, et rien n'exige qu'ils coïncident si la config déclare un
    # `ModelRef` incohérent avec son `runtime` ; dans toute config valide
    # aujourd'hui (`config.yaml`), les deux s'accordent.


@dataclass(frozen=True)
class CatalogModel:
    """`ModelCandidate` (déjà réel, `apex.models.registry`) enrichi de
    capacités dérivées, de disponibilité, de provenance et d'un historique
    — toujours vide tant qu'aucune vraie source n'est câblée."""

    candidate: ModelCandidate
    provider_id: str
    capabilities: frozenset[Capability]
    availability: Availability
    discovery: DiscoverySource
    history: PerformanceHistory = field(default_factory=PerformanceHistory)

    @property
    def model_id(self) -> str:
        return self.candidate.ref.id

    @property
    def display_name(self) -> str:
        return f"{self.candidate.ref.family}:{self.candidate.ref.version}"


def _provider_id(runtime: str, url: str) -> str:
    return f"{runtime}:{url}"


def _display_name_for_provider(runtime: str, url: str) -> str:
    if runtime == "ollama":
        return "Ollama (local)"
    host = urlparse(url).netloc or url
    return host


def _availability_for(candidate: ModelCandidate) -> Availability:
    if candidate.endpoint is None:
        return Availability.UNKNOWN
    if not candidate.endpoint.enabled:
        return Availability.DISABLED
    return Availability.UNKNOWN  # déclaré, jamais encore sondé en direct


def _history_from_stats(stats: ModelPerformanceStats) -> PerformanceHistory:
    return PerformanceHistory(
        usage_count=stats.usage_count, success_count=stats.success_count, failure_count=stats.failure_count,
        estimated_cost=Measurement.unknown(),  # aucune source de coût déclarée nulle part (ModelEndpoint/ModelRef)
        observed_latency_s=stats.mean_latency_s,
        last_failure_reason=stats.last_failure_reason, failure_reasons=dict(stats.failure_reasons),
    )


def build_model_catalog(
    config: ApexConfig, *, performance: Mapping[str, ModelPerformanceStats] | None = None,
) -> tuple[CatalogModel, ...]:
    """Vue STATIQUE — aucun appel réseau. `availability` reste `UNKNOWN`
    pour tout candidat non désactivé tant que `discover_live()` n'a pas été
    appelé explicitement dessus.

    `performance` (passe « Routing & Execution Observability Hardening »,
    N+18) — optionnel, même patron qu'`apex.routing.router.select_model` :
    un appelant qui a réellement agrégé un historique
    (`apex.agent_registry.performance.aggregate_model_performance`, depuis
    de vraies `AgentExecution`) peut l'injecter ici pour peupler
    `CatalogModel.history` — jamais recalculé ni dupliqué par ce module,
    qui reste sans dépendance DB. Omis (`None`), `history` reste la
    `PerformanceHistory` vide par défaut — exactement le comportement
    d'avant cette passe."""
    perf_map = performance or {}
    catalog: list[CatalogModel] = []
    for candidate in resolve_candidates(config):
        provider_id = (
            _provider_id(candidate.endpoint.runtime, candidate.endpoint.url)
            if candidate.endpoint is not None
            else _provider_id("unconfigured", "")
        )
        stats = perf_map.get(candidate.ref.id)
        catalog.append(
            CatalogModel(
                candidate=candidate, provider_id=provider_id,
                capabilities=derive_capabilities(candidate),
                availability=_availability_for(candidate),
                discovery=DiscoverySource.CONFIGURED,
                history=_history_from_stats(stats) if stats is not None else PerformanceHistory(),
            )
        )
    return tuple(catalog)


def build_provider_catalog(config: ApexConfig) -> tuple[ProviderInfo, ...]:
    """Groupe les `ModelEndpoint` réels par (`runtime`, `url`, `secret_key`)
    — jamais une seconde liste indépendante : chaque champ vient
    directement d'un `ModelEndpoint` déjà présent dans `config.endpoints`."""
    groups: dict[tuple[str, str, str | None], list[str]] = {}
    enabled_by_group: dict[tuple[str, str, str | None], bool] = {}
    secret_by_group: dict[tuple[str, str, str | None], str | None] = {}
    for endpoint in config.endpoints:
        key = (endpoint.runtime, endpoint.url, endpoint.secret_key)
        groups.setdefault(key, []).append(endpoint.served_model)
        enabled_by_group[key] = enabled_by_group.get(key, False) or endpoint.enabled
        secret_by_group[key] = endpoint.secret_key

    providers: list[ProviderInfo] = []
    for (runtime, url, secret_key), model_ids in groups.items():
        providers.append(
            ProviderInfo(
                id=_provider_id(runtime, url),
                display_name=_display_name_for_provider(runtime, url),
                runtime=runtime, base_url=url, secret_key=secret_key,
                local=(runtime == "ollama"),
                enabled=enabled_by_group[(runtime, url, secret_key)],
                model_ids=tuple(model_ids),
            )
        )
    return tuple(sorted(providers, key=lambda p: p.id))


# -- filtres -----------------------------------------------------------------


def filter_by_capabilities(models: Iterable[CatalogModel], required: frozenset[Capability]) -> tuple[CatalogModel, ...]:
    return tuple(m for m in models if required <= m.capabilities)


def filter_by_availability(models: Iterable[CatalogModel], *, allowed: frozenset[Availability]) -> tuple[CatalogModel, ...]:
    return tuple(m for m in models if m.availability in allowed)


def filter_by_runtime(models: Iterable[CatalogModel], runtime: str) -> tuple[CatalogModel, ...]:
    return tuple(m for m in models if m.candidate.endpoint is not None and m.candidate.endpoint.runtime == runtime)


def filter_local(models: Iterable[CatalogModel], *, local: bool = True) -> tuple[CatalogModel, ...]:
    return tuple(m for m in models if m.candidate.is_local == local)


def filter_by_min_context(models: Iterable[CatalogModel], min_context: int) -> tuple[CatalogModel, ...]:
    return tuple(
        m for m in models if m.candidate.endpoint is not None and m.candidate.endpoint.max_context >= min_context
    )


def usable_candidates(models: Iterable[CatalogModel]) -> tuple[ModelCandidate, ...]:
    """Extrait les `ModelCandidate` des entrées NI `DISABLED` NI
    `UNAVAILABLE` — le pont vers `apex.routing.select_model`, qui continue
    de faire son propre filtrage (capacité/contexte/`ExternalPolicy`) sans
    rien dupliquer ici. `UNKNOWN` reste inclus : un candidat jamais sondé
    n'est pas pour autant refusé — `select_model` décidera sur les critères
    qui lui reviennent."""
    return tuple(
        m.candidate for m in models
        if m.availability not in (Availability.DISABLED, Availability.UNAVAILABLE)
    )


# -- découverte en direct (réseau réel, jamais implicite) --------------------


def discover_live(
    models: Sequence[CatalogModel],
    *,
    provider_factory: Callable[[CatalogModel], ModelProvider] | None = None,
) -> tuple[CatalogModel, ...]:
    """SEUL point de ce module qui touche le réseau — jamais appelé
    implicitement par `build_model_catalog`. Un candidat `DISABLED` ou sans
    `ModelEndpoint` n'est jamais sondé (`UNKNOWN` conservé pour le second
    cas — rien à interroger ; `DISABLED` conservé tel quel pour le
    premier). Le résultat est TOUJOURS `AVAILABLE`/`UNAVAILABLE` réel pour
    les autres, jamais une supposition."""
    resolved: list[CatalogModel] = []
    for model in models:
        if model.availability is Availability.DISABLED or model.candidate.endpoint is None:
            resolved.append(model)
            continue
        provider = (
            provider_factory(model) if provider_factory is not None
            else get_default_provider(model.candidate.endpoint)
        )
        try:
            report = provider.detect()
        except (ModelProviderError, TimeoutExceeded):
            resolved.append(_with_availability(model, Availability.UNAVAILABLE, DiscoverySource.CONFIGURED))
            continue
        if report.is_available:
            resolved.append(_with_availability(model, Availability.AVAILABLE, DiscoverySource.DISCOVERED))
        else:
            resolved.append(_with_availability(model, Availability.UNAVAILABLE, DiscoverySource.CONFIGURED))
    return tuple(resolved)


def _with_availability(model: CatalogModel, availability: Availability, discovery: DiscoverySource) -> CatalogModel:
    return CatalogModel(
        candidate=model.candidate, provider_id=model.provider_id, capabilities=model.capabilities,
        availability=availability, discovery=discovery, history=model.history,
    )
