"""Contrat `ModelProvider` — même patron que `apex.sandbox.provider`/
`apex.artifacts.provider` (étapes 7/10) : un contrat abstrait minimal, un
seul point de choix de backend (`get_default_provider`), rien d'autre.

Périmètre strict de cette étape (Phase 6, ModelProvider partiel) : PAS de
résolution rôle -> endpoint (`Placement`, PLANNED), PAS d'`EmbeddingProvider`,
PAS de raccordement à `MissionEngine`/`AgentRuntime`. L'appelant fournit
TOUJOURS un `ModelEndpoint` déjà résolu — aucun modèle n'est jamais codé en
dur ici.

Le streaming (`generate_stream`) est délibérément HORS de ce contrat abstrait
— décision explicite (validation Phase 6) : c'est une méthode concrète de
`OllamaProvider`, généralisable au contrat plus tard seulement si plusieurs
backends en ont réellement besoin, jamais anticipée.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport


class ModelProvider(ABC):
    @abstractmethod
    def detect(self) -> ModelCapabilityReport:
        """Sonde (empiriquement, jamais supposée) la disponibilité réelle de
        ce backend pour ce endpoint précis. Ne lève jamais."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Appelle `self.detect()` en interne avant toute tentative — même
        discipline que `LinuxNamespaceSandbox.execute()` : jamais une
        tentative sur un backend dont l'indisponibilité est déjà constatée.
        Lève `apex.errors.ModelProviderError` si le backend n'est pas
        `AVAILABLE`, ou si l'appel réel échoue ; `apex.errors.
        TimeoutExceeded` si `request.timeout_s` est dépassé. Ne retourne
        JAMAIS un résultat inventé."""


def get_default_provider(endpoint: ModelEndpoint) -> ModelProvider:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un backend différent (vLLM, un
    fournisseur distant) serait choisi une fois réellement implémenté.
    Aujourd'hui : `OllamaProvider`, le seul backend réel."""
    from apex.models.ollama import OllamaProvider

    return OllamaProvider(endpoint)
