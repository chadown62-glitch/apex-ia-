"""Tests d'`apex.routing.generation` — `generate_with_fallback`/
`generate_all_candidates`, contre des doubles de test fidèles au contrat
`ModelProvider` (jamais un vrai modèle — voir `tests/
test_missions_model_generation.py` pour la même discipline déjà établie).
"""

from __future__ import annotations

import unittest

from apex.config.schema import ModelEndpoint, ModelRef
from apex.errors import ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.routing.generation import generate_all_candidates, generate_with_fallback


class _FakeProvider(ModelProvider):
    """Double de test fidèle au contrat `ModelProvider` — jamais un vrai
    modèle. `mode` contrôle le comportement : "ok" / "provider_error" /
    "timeout"."""

    def __init__(self, *, mode: str = "ok", text: str = "réponse factice") -> None:
        self.mode = mode
        self.text = text
        self.calls = 0

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="fake", detail="double de test")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        if self.mode == "provider_error":
            raise ModelProviderError("FakeProvider.generate", endpoint_url=request.endpoint.url, detail="échec simulé")
        if self.mode == "timeout":
            raise TimeoutExceeded("FakeProvider.generate", request.timeout_s)
        return GenerationResult(
            text=self.text, model_used=request.endpoint.served_model,
            tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
            finish_reason="stop", elapsed_s=0.001, facts={},
        )


def a_ref(**overrides) -> ModelRef:
    defaults = dict(family="qwen2.5-coder", version="7b-instruct", quantization="q4_k_m", capability_class="coder")
    defaults.update(overrides)
    return ModelRef(**defaults)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="qwen2.5-coder:7b-instruct", max_context=32768,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def a_candidate(**overrides) -> ModelCandidate:
    defaults = dict(role="coder_main", ref=a_ref(), resident=False, endpoint=an_endpoint())
    defaults.update(overrides)
    return ModelCandidate(**defaults)


class TestGenerateWithFallback(unittest.TestCase):
    def test_succeeds_on_the_first_candidate(self) -> None:
        provider = _FakeProvider(mode="ok", text="première réponse")
        result = generate_with_fallback(
            [a_candidate()], prompt="x", provider_factory=lambda c: provider,
        )
        self.assertEqual(result.result.text, "première réponse")
        self.assertEqual(len(result.attempts), 1)
        self.assertTrue(result.attempts[0].succeeded)

    def test_falls_back_to_the_second_candidate_on_provider_error(self) -> None:
        failing = _FakeProvider(mode="provider_error")
        working = _FakeProvider(mode="ok", text="réponse du second")
        providers = {"first": failing, "second": working}
        first = a_candidate(role="first", endpoint=an_endpoint(served_model="model-a"))
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        result = generate_with_fallback(
            [first, second], prompt="x", provider_factory=lambda c: providers[c.role],
        )
        self.assertEqual(result.result.text, "réponse du second")
        self.assertEqual(result.succeeded_with, second)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].succeeded)
        self.assertTrue(result.attempts[1].succeeded)

    def test_falls_back_on_timeout_too(self) -> None:
        timing_out = _FakeProvider(mode="timeout")
        working = _FakeProvider(mode="ok")
        providers = {"first": timing_out, "second": working}
        first = a_candidate(role="first", endpoint=an_endpoint(served_model="model-a"))
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        result = generate_with_fallback([first, second], prompt="x", provider_factory=lambda c: providers[c.role])
        self.assertIsNotNone(result.result)
        self.assertEqual(result.succeeded_with, second)

    def test_original_error_is_never_masked_when_all_candidates_fail(self) -> None:
        p1 = _FakeProvider(mode="provider_error")
        p2 = _FakeProvider(mode="timeout")
        providers = {"first": p1, "second": p2}
        first = a_candidate(role="first", endpoint=an_endpoint(served_model="model-a"))
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        result = generate_with_fallback([first, second], prompt="x", provider_factory=lambda c: providers[c.role])
        self.assertIsNone(result.result)
        self.assertIsNone(result.succeeded_with)
        self.assertEqual(len(result.attempts), 2)
        self.assertIsNotNone(result.attempts[0].error)
        self.assertIsNotNone(result.attempts[1].error)
        self.assertNotEqual(result.attempts[0].error, result.attempts[1].error)

    def test_a_candidate_without_endpoint_is_recorded_as_a_failed_attempt_not_a_crash(self) -> None:
        no_endpoint = a_candidate(endpoint=None)
        working = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))
        provider = _FakeProvider(mode="ok")

        result = generate_with_fallback(
            [no_endpoint, working], prompt="x", provider_factory=lambda c: provider,
        )
        self.assertEqual(result.succeeded_with, working)
        self.assertFalse(result.attempts[0].succeeded)
        self.assertIn("aucun ModelEndpoint", result.attempts[0].error)

    def test_stops_at_first_success_never_calls_later_candidates(self) -> None:
        provider = _FakeProvider(mode="ok")
        first = a_candidate(role="first")
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        generate_with_fallback([first, second], prompt="x", provider_factory=lambda c: provider)
        self.assertEqual(provider.calls, 1)


class TestGenerateAllCandidates(unittest.TestCase):
    def test_queries_every_candidate_even_after_a_success(self) -> None:
        provider = _FakeProvider(mode="ok")
        first = a_candidate(role="first")
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        attempts = generate_all_candidates([first, second], prompt="x", provider_factory=lambda c: provider)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(provider.calls, 2)
        self.assertTrue(all(a.succeeded for a in attempts))

    def test_mixed_success_and_failure_all_recorded(self) -> None:
        working = _FakeProvider(mode="ok", text="réponse A")
        failing = _FakeProvider(mode="provider_error")
        providers = {"first": working, "second": failing}
        first = a_candidate(role="first")
        second = a_candidate(role="second", endpoint=an_endpoint(served_model="model-b"))

        attempts = generate_all_candidates([first, second], prompt="x", provider_factory=lambda c: providers[c.role])
        self.assertTrue(attempts[0].succeeded)
        self.assertEqual(attempts[0].result.text, "réponse A")
        self.assertFalse(attempts[1].succeeded)

    def test_never_auto_selects_a_best_response(self) -> None:
        """Infrastructure seule — aucune sélection de « meilleure » réponse
        (lacune documentée, pas comblée par une heuristique improvisée)."""
        provider = _FakeProvider(mode="ok")
        attempts = generate_all_candidates([a_candidate()], prompt="x", provider_factory=lambda c: provider)
        self.assertIsInstance(attempts, tuple)
        # pas de champ "best"/"selected" sur le retour — juste la liste brute
        self.assertFalse(hasattr(attempts, "best"))


if __name__ == "__main__":
    unittest.main()
