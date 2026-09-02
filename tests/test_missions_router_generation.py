"""Tests d'`apex.missions.execution.generate_mission_response_via_router` —
le TROISIÈME chemin d'exécution de Mission, câblé au routeur
(`apex.routing.select_model` + `generate_with_fallback`).

Contre des doubles de test fidèles au contrat `ModelProvider` — jamais un
vrai modèle. `generate_mission_response` (existant) n'est PAS modifiée :
`tests/test_missions_model_generation.py` continue de passer sans
changement (vérifié séparément), preuve de compatibilité ascendante.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.config.schema import ModelEndpoint, ModelRef
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError, PolicyDenied
from apex.missions.execution import generate_mission_response_via_router
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject
from apex.routing.model import Capability, RoutingRequest


class _FakeProvider(ModelProvider):
    def __init__(self, *, mode: str = "ok", text: str = "réponse factice") -> None:
        self.mode = mode
        self.text = text
        self.received_requests: list[GenerationRequest] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="fake", detail="double de test")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.received_requests.append(request)
        if self.mode == "provider_error":
            raise ModelProviderError("FakeProvider.generate", endpoint_url=request.endpoint.url, detail="échec simulé")
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


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestGenerateMissionResponseViaRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.mission_service = MissionService(self.factory)
        self.mission = self.mission_service.create(
            title="Factorielle", description="d", specialty="research",
            objective="Écris une fonction Python qui calcule la factorielle.",
            created_by=Subject.HUMAN,
        )

    def test_selects_and_generates_successfully(self) -> None:
        provider = _FakeProvider(text="réponse réelle du routeur")
        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
            provider_factory=lambda c: provider,
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertEqual(final.result, "réponse réelle du routeur")

    def test_no_eligible_candidate_marks_mission_failed(self) -> None:
        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[],
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertIn("aucun modèle disponible", final.error)

    def test_required_capability_not_met_marks_mission_failed(self) -> None:
        judge_only = a_candidate(role="judge", ref=a_ref(family="gemma4", capability_class="judge"))
        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[judge_only],
            routing_request=RoutingRequest(required_capabilities=frozenset({Capability.CODING})),
        )
        self.assertEqual(final.state, MissionState.FAILED)

    def test_fallback_to_second_candidate_on_first_failure(self) -> None:
        failing = _FakeProvider(mode="provider_error")
        working = _FakeProvider(text="second réussi")
        providers = {"a_role": failing, "b_role": working}
        first = a_candidate(role="a_role")
        second = a_candidate(role="b_role", endpoint=an_endpoint(served_model="model-b"))

        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[first, second],
            provider_factory=lambda c: providers[c.role],
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertEqual(final.result, "second réussi")

    def test_all_candidates_failing_marks_mission_failed_with_full_detail(self) -> None:
        failing = _FakeProvider(mode="provider_error")
        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
            provider_factory=lambda c: failing,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertIn("tous les candidats ont échoué", final.error)

    def test_a_clone_subject_cannot_trigger_router_generation(self) -> None:
        with self.assertRaises(PolicyDenied):
            generate_mission_response_via_router(
                self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
                requested_by=Subject.RESEARCH,
            )

    def test_unknown_mission_raises(self) -> None:
        with self.assertRaises(LookupError):
            generate_mission_response_via_router(
                "does-not-exist", mission_service=self.mission_service, candidates=[a_candidate()],
            )

    def test_mission_objective_is_used_as_default_prompt(self) -> None:
        provider = _FakeProvider()
        generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
            provider_factory=lambda c: provider,
        )
        self.assertEqual(len(provider.received_requests), 1)
        self.assertEqual(provider.received_requests[0].prompt, self.mission.objective)

    def test_an_explicit_prompt_overrides_the_objective(self) -> None:
        provider = _FakeProvider()
        generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
            provider_factory=lambda c: provider, prompt="prompt explicite",
        )
        self.assertEqual(provider.received_requests[0].prompt, "prompt explicite")

    def test_never_jumps_directly_to_completed(self) -> None:
        provider = _FakeProvider()
        final = generate_mission_response_via_router(
            self.mission.id, mission_service=self.mission_service, candidates=[a_candidate()],
            provider_factory=lambda c: provider,
        )
        self.assertNotEqual(final.state, MissionState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
