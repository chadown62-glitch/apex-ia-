"""`probe_physical_availability` — sonde EMPIRIQUEMENT la disponibilité
PHYSIQUE des `ModelCandidate` déjà résolus (`apex.models.registry.
resolve_candidates`), avant qu'`apex.routing.router.select_model` ne les
score.

**Le bug que corrige ce module (diagnostic exact)** : `ModelCandidate.
is_available` (`apex.models.registry`) répond « un `ModelEndpoint` est-il
DÉCLARÉ pour ce candidat ? » — jamais « ce endpoint répond-il RÉELLEMENT,
avec ce modèle RÉELLEMENT chargé ? ». `apex.routing.router._passes_filters`
consomme ce booléen tel quel : un modèle déclaré dans la config mais jamais
installé sur l'instance Ollama réelle est donc éligible, sélectionné, puis
échoue seulement à la génération — trop tard pour un routing initial propre.

**Pourquoi ni `apex.models.registry` ni `apex.routing.router`** : les deux
portent un contrat documenté et testé de PURETÉ — `resolve_candidates` est
explicitement « des requêtes PURES ... rien de plus » (aucune I/O), et
`select_model` garantit une non-régression stricte, testée, pour tout
appelant qui omet ses paramètres optionnels (aucune I/O n'y a jamais été
ajoutée). Ajouter un appel réseau dans l'un ou l'autre romprait ces deux
contrats. La sonde physique appartient à la couche qui compose DÉJÀ des
primitives réelles avec des budgets explicites — `apex.taskloop.loop.
run_task_loop`, qui l'invoque ici, avant `select_model`, exactement comme
suggéré par la mission (« orchestration du TaskLoop avant select_model »).

**Ce que ce module NE fait PAS** : il ne réimplémente AUCUNE logique de
sonde — il appelle `ModelProvider.detect()` (`apex.models.provider`), déjà
un contrat réel, borné (`OllamaProvider.detect()` : `GET /api/tags`,
timeout 2s par défaut, "Ne lève jamais"), déjà utilisé par `ModelProvider.
generate()` lui-même. Aucun nom de modèle n'est jamais codé en dur ici :
la liste sondée est entièrement celle que l'appelant fournit (déclarée en
config, ou injectée par un test).

**Borné** : au plus UN appel `detect()` par paire distincte `(endpoint.url,
endpoint.served_model)` — plusieurs `ModelCandidate` (rôles différents)
pointant vers le même endpoint physique ne sont jamais sondés deux fois.

**Un candidat sans `ModelEndpoint` déclaré (`is_available` False) n'est
jamais sondé** : rien de physique n'existe à interroger pour lui, et
`apex.routing.router` l'exclut déjà avec sa propre raison auditable
(`"aucun ModelEndpoint réel déclaré..."`, visible dans `RoutingDecision.
ranked_candidates`) — ce module le laisse donc passer INCHANGÉ vers
`select_model`, pour ne jamais perdre cette explication existante."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from apex.models.model import ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate


@dataclass(frozen=True, slots=True)
class CandidatePhysicalStatus:
    """Un candidat EXCLU par la sonde physique, avec le rapport réel
    (`ModelProvider.detect()`) qui justifie l'exclusion — jamais un simple
    booléen : `report.detail`/`report.facts` restent la preuve auditable."""

    candidate: ModelCandidate
    report: ModelCapabilityReport


@dataclass(frozen=True, slots=True)
class PhysicalAvailabilityResult:
    """`survivors` : candidats à transmettre à `select_model` (physiquement
    confirmés, OU déclarativement déjà sans endpoint — voir docstring du
    module). `excluded` : candidats déclarés avec un endpoint réel, mais
    dont la sonde physique a constaté l'indisponibilité. `probed_endpoints`
    : nombre d'appels `detect()` RÉELLEMENT effectués (déduplication par
    endpoint physique distinct) — l'observable qui prouve que la sonde est
    bornée."""

    survivors: tuple[ModelCandidate, ...]
    excluded: tuple[CandidatePhysicalStatus, ...]
    probed_endpoints: int


def probe_physical_availability(
    candidates: Sequence[ModelCandidate],
    *,
    provider_factory: Callable[[ModelCandidate], ModelProvider],
) -> PhysicalAvailabilityResult:
    """Sonde `candidates` avec `provider_factory` (même fabrique que celle
    utilisée par l'appelant pour la génération — même discipline
    d'injection de double de test que `apex.taskloop.loop.
    _default_provider_factory`, jamais un second mécanisme de construction
    de `ModelProvider`). Ne lève jamais : `ModelProvider.detect()` est
    documenté « ne lève jamais » et ce module ne fait qu'agréger ses
    rapports."""
    reports_by_endpoint: dict[tuple[str, str], ModelCapabilityReport] = {}
    survivors: list[ModelCandidate] = []
    excluded: list[CandidatePhysicalStatus] = []

    for candidate in candidates:
        if candidate.endpoint is None:
            survivors.append(candidate)
            continue
        key = (candidate.endpoint.url, candidate.endpoint.served_model)
        if key not in reports_by_endpoint:
            provider = provider_factory(candidate)
            reports_by_endpoint[key] = provider.detect()
        report = reports_by_endpoint[key]
        if report.is_available:
            survivors.append(candidate)
        else:
            excluded.append(CandidatePhysicalStatus(candidate=candidate, report=report))

    return PhysicalAvailabilityResult(
        survivors=tuple(survivors), excluded=tuple(excluded), probed_endpoints=len(reports_by_endpoint),
    )
