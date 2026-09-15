"""Tests de `apex.routing.availability.probe_physical_availability` — la
correction du bug diagnostiqué en mission N+27 (correction physique du
routing) : `ModelCandidate.is_available` est déclaratif (endpoint présent
en config), jamais une preuve de disponibilité PHYSIQUE réelle. Ce module
sonde `ModelProvider.detect()` (contrat réel, jamais réimplémenté ici) et
ne doit jamais : sonder un candidat sans endpoint, sonder deux fois le même
endpoint physique, ou modifier `apex.models.registry`/`apex.routing.router`
(tous deux hors de portée de ces tests — non touchés par cette mission)."""

from __future__ import annotations

import unittest

from apex.config.schema import ModelEndpoint, ModelRef
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.routing.availability import probe_physical_availability


class _FakeProbeProvider(ModelProvider):
    """Double fidèle au contrat `ModelProvider` — seul `detect()` importe
    ici. `calls` compte les invocations RÉELLES de `detect()` pour prouver
    la déduplication par endpoint physique."""

    def __init__(self, *, status: CapabilityStatus, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        self.calls = 0

    def detect(self) -> ModelCapabilityReport:
        self.calls += 1
        return ModelCapabilityReport(status=self.status, backend="fake-probe", detail=self.detail)

    def generate(self, request: GenerationRequest) -> GenerationResult:  # pragma: no cover - inutilisé ici
        return GenerationResult(
            text="", model_used=request.endpoint.served_model, tokens_prompt=Measurement.unknown(),
            tokens_completion=Measurement.unknown(), finish_reason="stop", elapsed_s=0.0, facts={},
        )


def a_ref(**overrides) -> ModelRef:
    defaults = dict(family="a-model", version="1", quantization="q4_k_m", capability_class="coder")
    defaults.update(overrides)
    return ModelRef(**defaults)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="a-model:1", max_context=8192,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def a_candidate(**overrides) -> ModelCandidate:
    defaults = dict(role="coder_main", ref=a_ref(), resident=False, endpoint=an_endpoint())
    defaults.update(overrides)
    return ModelCandidate(**defaults)


class TestNoEndpointNeverProbed(unittest.TestCase):
    def test_a_candidate_without_endpoint_passes_through_unprobed(self) -> None:
        candidate = a_candidate(endpoint=None)
        calls: list[str] = []

        def factory(c: ModelCandidate) -> ModelProvider:
            calls.append(c.ref.id)
            return _FakeProbeProvider(status=CapabilityStatus.AVAILABLE)

        result = probe_physical_availability([candidate], provider_factory=factory)

        self.assertEqual(result.survivors, (candidate,))
        self.assertEqual(result.excluded, ())
        self.assertEqual(result.probed_endpoints, 0)
        self.assertEqual(calls, [])


class TestPhysicallyAvailable(unittest.TestCase):
    def test_a_reachable_candidate_survives(self) -> None:
        candidate = a_candidate()
        provider = _FakeProbeProvider(status=CapabilityStatus.AVAILABLE)

        result = probe_physical_availability([candidate], provider_factory=lambda c: provider)

        self.assertEqual(result.survivors, (candidate,))
        self.assertEqual(result.excluded, ())
        self.assertEqual(result.probed_endpoints, 1)
        self.assertEqual(provider.calls, 1)


class TestPhysicallyUnavailable(unittest.TestCase):
    def test_an_unreachable_or_absent_model_is_excluded_with_a_real_report(self) -> None:
        candidate = a_candidate()
        provider = _FakeProbeProvider(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV, detail="modèle absent de la machine",
        )

        result = probe_physical_availability([candidate], provider_factory=lambda c: provider)

        self.assertEqual(result.survivors, ())
        self.assertEqual(len(result.excluded), 1)
        self.assertIs(result.excluded[0].candidate, candidate)
        self.assertEqual(result.excluded[0].report.detail, "modèle absent de la machine")
        self.assertEqual(result.probed_endpoints, 1)


class TestDeduplicationByPhysicalEndpoint(unittest.TestCase):
    def test_two_candidates_sharing_the_same_endpoint_are_probed_once(self) -> None:
        shared_endpoint = an_endpoint()
        candidate_a = a_candidate(role="coder_main", endpoint=shared_endpoint)
        candidate_b = a_candidate(role="reasoner", ref=a_ref(capability_class="reasoning"), endpoint=shared_endpoint)
        provider = _FakeProbeProvider(status=CapabilityStatus.AVAILABLE)
        factory_calls = 0

        def factory(c: ModelCandidate) -> ModelProvider:
            nonlocal factory_calls
            factory_calls += 1
            return provider

        result = probe_physical_availability([candidate_a, candidate_b], provider_factory=factory)

        self.assertEqual(set(result.survivors), {candidate_a, candidate_b})
        self.assertEqual(result.probed_endpoints, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(factory_calls, 1)

    def test_two_candidates_on_distinct_endpoints_are_each_probed(self) -> None:
        candidate_a = a_candidate(endpoint=an_endpoint(served_model="a-model:1"))
        candidate_b = a_candidate(
            ref=a_ref(family="b-model"), endpoint=an_endpoint(served_model="b-model:1"),
        )
        providers = {
            "a-model:1": _FakeProbeProvider(status=CapabilityStatus.AVAILABLE),
            "b-model:1": _FakeProbeProvider(status=CapabilityStatus.UNAVAILABLE_ON_DEV, detail="absent"),
        }

        def factory(c: ModelCandidate) -> ModelProvider:
            return providers[c.endpoint.served_model]

        result = probe_physical_availability([candidate_a, candidate_b], provider_factory=factory)

        self.assertEqual(result.survivors, (candidate_a,))
        self.assertEqual(len(result.excluded), 1)
        self.assertIs(result.excluded[0].candidate, candidate_b)
        self.assertEqual(result.probed_endpoints, 2)
        self.assertEqual(providers["a-model:1"].calls, 1)
        self.assertEqual(providers["b-model:1"].calls, 1)


class TestEmptyInput(unittest.TestCase):
    def test_no_candidates_never_calls_the_factory(self) -> None:
        calls: list[str] = []

        def factory(c: ModelCandidate) -> ModelProvider:
            calls.append(c.ref.id)
            return _FakeProbeProvider(status=CapabilityStatus.AVAILABLE)

        result = probe_physical_availability([], provider_factory=factory)

        self.assertEqual(result.survivors, ())
        self.assertEqual(result.excluded, ())
        self.assertEqual(result.probed_endpoints, 0)
        self.assertEqual(calls, [])


class TestGenericAcrossRuntimes(unittest.TestCase):
    """Aucun runtime/nom de modèle n'est jamais codé en dur dans
    `probe_physical_availability` — la sonde fonctionne identiquement pour
    n'importe quel `runtime` déclaré (`ollama`, `openai_compatible`, ou
    tout autre futur backend), puisqu'elle ne connaît que le contrat
    abstrait `ModelProvider.detect()`, jamais un backend concret."""

    def test_a_non_ollama_runtime_is_probed_the_same_way(self) -> None:
        candidate = a_candidate(
            endpoint=an_endpoint(runtime="openai_compatible", url="https://api.example.invalid"),
        )
        provider = _FakeProbeProvider(status=CapabilityStatus.AVAILABLE)

        result = probe_physical_availability([candidate], provider_factory=lambda c: provider)

        self.assertEqual(result.survivors, (candidate,))
        self.assertEqual(result.probed_endpoints, 1)


if __name__ == "__main__":
    unittest.main()
