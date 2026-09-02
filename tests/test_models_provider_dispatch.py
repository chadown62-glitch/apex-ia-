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

    def test_unknown_runtime_falls_back_to_openai_compatible_never_ollama(self) -> None:
        """Un `runtime` non reconnu ne doit jamais silencieusement devenir
        Ollama — le seul autre backend réel est le générique
        OpenAI-compatible."""
        provider = get_default_provider(an_endpoint(runtime="vllm", url="https://vllm.example.com/v1"))
        self.assertIsInstance(provider, OpenAICompatibleProvider)


if __name__ == "__main__":
    unittest.main()
