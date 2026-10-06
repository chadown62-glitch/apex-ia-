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
    c'est ICI, et seulement ici, qu'un backend différent est choisi, selon
    `endpoint.runtime` (déjà le champ prévu pour ça, `apex.config.schema`).
    `"ollama"` -> `OllamaProvider` (réel, Phase 6). Tout le reste
    (`"openai_compatible"`, mais aussi les alias usuels des fournisseurs
    compatibles) -> `OpenAICompatibleProvider` (réel, un seul adapter pour
    OpenAI/OpenRouter/Together/Groq/Fireworks/DeepSeek/Mistral/xAI/Qwen en
    mode compatible — même contrat HTTP, jamais une classe par
    fournisseur). Aucun autre backend n'est réel aujourd'hui — `"vllm"`
    resterait `OpenAICompatibleProvider` (l'API vLLM native est déjà
    compatible OpenAI) tant qu'un besoin réel n'exige pas davantage."""
    from apex.models.backends import BackendStatus, backend_for
    from apex.models.ollama import OllamaProvider
    from apex.models.openai_compatible import OpenAICompatibleProvider

    # Catalogue FERMÉ (`apex.models.backends`, 06/10/2026) : un runtime PLANNED (airllm, transformers) ou
    # inconnu n'est plus « deviné » OpenAI-compatible — il obtient un fournisseur honnêtement indisponible.
    backend = backend_for(endpoint.runtime)
    if backend is None or backend.status is not BackendStatus.IMPLEMENTED:
        return UnavailableRuntimeProvider(endpoint)
    if backend.adapter == "ollama":
        return OllamaProvider(endpoint)
    return OpenAICompatibleProvider(endpoint)


class UnavailableRuntimeProvider(ModelProvider):
    """Runtime PLANNED ou inconnu : `detect()` le dit (PLANNED/UNSUPPORTED), `generate()` refuse — jamais une
    réponse simulée, jamais un appel réseau vers un serveur qui ne parle pas ce protocole."""

    def __init__(self, endpoint: ModelEndpoint) -> None:
        self._endpoint = endpoint

    def detect(self) -> ModelCapabilityReport:
        from apex.models.backends import backend_for
        from apex.obs.capability import CapabilityStatus

        backend = backend_for(self._endpoint.runtime)
        if backend is None:
            return ModelCapabilityReport(
                status=CapabilityStatus.UNSUPPORTED, backend=self._endpoint.runtime,
                detail=f"runtime « {self._endpoint.runtime} » inconnu (catalogue apex.models.backends)",
            )
        return ModelCapabilityReport(
            status=CapabilityStatus.PLANNED, backend=backend.runtime,
            detail=f"runtime « {backend.runtime} » PLANNED : {backend.description}",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from apex.errors import ModelProviderError

        raise ModelProviderError(
            "UnavailableRuntimeProvider.generate", endpoint_url=self._endpoint.url,
            detail=self.detect().detail,
        )
