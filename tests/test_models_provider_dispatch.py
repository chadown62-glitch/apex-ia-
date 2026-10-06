"""Tests d'`apex.models.provider.get_default_provider` — le SEUL point de
choix de backend `ModelProvider`, dispatché sur `ModelEndpoint.runtime`.
"""

from __future__ import annotations

import unittest

from apex.config.schema import ModelEndpoint
from apex.models.ollama import OllamaProvider
from apex.models.openai_compatible import OpenAICompatibleProvider
from apex.models.provider import get_default_provider


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="qwen2.5-coder:7b-instruct", max_context=32768,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class TestDispatchByRuntime(unittest.TestCase):
    def test_ollama_runtime_returns_ollama_provider(self) -> None:
        provider = get_default_provider(an_endpoint(runtime="ollama"))
        self.assertIsInstance(provider, OllamaProvider)

    def test_openai_compatible_runtime_returns_openai_compatible_provider(self) -> None:
        provider = get_default_provider(an_endpoint(runtime="openai_compatible", url="https://api.example.com/v1"))
        self.assertIsInstance(provider, OpenAICompatibleProvider)

    def test_known_openai_compatible_runtimes_use_the_generic_adapter_never_ollama(self) -> None:
        """vLLM/llama.cpp/LM Studio et les fournisseurs compatibles parlent l'API OpenAI : un seul adapter."""
        for runtime in ("vllm", "llama_cpp", "lmstudio", "external", "openrouter"):
            provider = get_default_provider(an_endpoint(runtime=runtime, url="https://vllm.example.com/v1"))
            self.assertIsInstance(provider, OpenAICompatibleProvider, runtime)

    def test_planned_or_unknown_runtimes_are_honestly_unavailable_never_guessed(self) -> None:
        """06/10/2026 : un runtime PLANNED (AirLLM, transformers en processus) ou inconnu n'est plus envoyé à
        l'adapter HTTP OpenAI : fournisseur indisponible, `generate` refuse sans aucun appel réseau."""
        from apex.errors import ModelProviderError
        from apex.models.model import GenerationRequest
        from apex.models.provider import UnavailableRuntimeProvider
        from apex.obs.capability import CapabilityStatus

        for runtime, status in (("airllm", CapabilityStatus.PLANNED),
                                ("mon_runtime_maison", CapabilityStatus.UNSUPPORTED)):
            endpoint = an_endpoint(runtime=runtime, url="http://127.0.0.1:9")
            provider = get_default_provider(endpoint)
            self.assertIsInstance(provider, UnavailableRuntimeProvider, runtime)
            report = provider.detect()
            self.assertEqual(report.status, status)
            self.assertFalse(report.is_available)
            with self.assertRaises(ModelProviderError):
                provider.generate(GenerationRequest(endpoint=endpoint, prompt="x"))

    def test_the_backend_catalogue_never_claims_a_planned_backend(self) -> None:
        from apex.models.backends import BACKENDS, BackendStatus

        for backend in BACKENDS:
            self.assertEqual(backend.adapter is None, backend.status is BackendStatus.PLANNED, backend.runtime)
        self.assertEqual({b.runtime for b in BACKENDS if b.status is BackendStatus.PLANNED}, {"airllm"})

    def test_the_transformers_runtime_is_the_native_in_process_backend(self) -> None:
        from apex.models.native import NativeTransformersProvider

        provider = get_default_provider(an_endpoint(runtime="transformers", url="file:///nulle/part"))
        self.assertIsInstance(provider, NativeTransformersProvider)
        self.assertFalse(provider.detect().is_available)  # poids absents : indisponible, jamais simulé

    def test_the_backend_catalogue_is_exposed_as_is(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app
        from tests.api_test_support import TEST_API_TOKEN

        client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
        data = client.get("/api/v1/models/backends").json()["data"]
        by_runtime = {b["runtime"]: b for b in data}
        self.assertEqual(by_runtime["airllm"]["status"], "planned")
        self.assertIsNone(by_runtime["airllm"]["adapter"])
        self.assertEqual(by_runtime["ollama"]["adapter"], "ollama")


if __name__ == "__main__":
    unittest.main()
