"""Contrat `ResearchProvider` — recherche structurée pour l'étape RESEARCH
du pipeline de conception. Même patron que `apex.artifacts.provider`/
`apex.models.provider` : une ABC, un seul point de construction
(`get_default_provider()`), et — ici — AUCUN backend réel connecté
aujourd'hui (voir `apex.interfaces.registry`, entrée `SearchProvider`,
`PLANNED`, fallback « désactivé » : le même vide, documenté depuis la
Phase 13, pas encore comblé pour aucun consommateur de ce dépôt).

`NullResearchProvider` n'est PAS un double de test : c'est le comportement
de production actuel, honnête — `detect()` répond `UNAVAILABLE_ON_DEV` et
`search()` retourne un tuple vide plutôt que de fabriquer des résultats.
Un futur backend réel (ex. une API de recherche configurée via
`apex.secrets`) s'ajouterait ICI, dans `get_default_provider()`, sans
modifier aucun consommateur (`apex.design.pipeline`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from apex.design.model import ResearchResult
from apex.obs.capability import CapabilityStatus


@dataclass(frozen=True)
class ResearchCapabilityReport:
    status: CapabilityStatus
    backend: str
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE


class ResearchProvider(ABC):
    @abstractmethod
    def detect(self) -> ResearchCapabilityReport:
        """Sonde la disponibilité RÉELLE d'un backend de recherche — jamais
        supposée. Ne lève jamais."""

    @abstractmethod
    def search(self, query: str, *, max_results: int = 5) -> tuple[ResearchResult, ...]:
        """Retourne des résultats RÉELLEMENT obtenus, ou un tuple vide si
        aucun backend n'est disponible — jamais une fabrication plausible."""


class NullResearchProvider(ResearchProvider):
    """Comportement honnête en l'absence de tout backend de recherche web
    réel dans ce dépôt (Phase 13, `SearchProvider`, toujours PLANNED)."""

    def detect(self) -> ResearchCapabilityReport:
        return ResearchCapabilityReport(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV,
            backend="null",
            detail=(
                "aucun backend de recherche web réel n'est configuré dans ce dépôt "
                "(apex.interfaces.registry: SearchProvider, PLANNED depuis la Phase 13) — "
                "la recherche retourne intentionnellement zéro résultat plutôt qu'une "
                "fabrication"
            ),
        )

    def search(self, query: str, *, max_results: int = 5) -> tuple[ResearchResult, ...]:
        return ()


def get_default_provider() -> ResearchProvider:
    """Seul point de construction — un futur backend réel (recherche web
    via une API configurée) serait choisi ICI, jamais par chaque
    consommateur individuellement."""
    return NullResearchProvider()
