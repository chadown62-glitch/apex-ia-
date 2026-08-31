"""Tests d'`apex.missions.execution.generate_mission_response` — le
DEUXIÈME chemin d'exécution d'une Mission (`ModelProvider`, HORS sandbox),
câblé contre un double de test fidèle au contrat `ModelProvider`.

IMPORTANT — ces tests prouvent le CÂBLAGE `MissionEngine -> ModelProvider`
dans CET environnement, PAS une inférence réelle : `_FakeModelProvider`
n'est jamais un modèle, seulement une implémentation fidèle du contrat
`ModelProvider` pour observer ce que `generate_mission_response` lui
envoie et comment elle traite ce qu'il renvoie. Le SEUL test contre un
VRAI Ollama est `TestRealOllamaMissionIntegration`, explicitement sauté si
aucun Ollama réel n'est joignable — jamais un faux PASS d'intégration
(même discipline que tests/test_models_ollama.py).
"""

from __future__ import annotations

import os
import socket
import unittest

from sqlalchemy import create_engine

from apex.config.schema import ModelEndpoint
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError, PolicyDenied
from apex.missions.execution import generate_mission_response
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(pool_id="local", runtime="ollama", url="http://127.0.0.1:11434", served_model="qwen3-coder:30b", max_context=8192)
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class _FakeModelProvider(ModelProvider):
    """Double de test — implémente fidèlement le contrat `ModelProvider`
    (jamais une classe parallèle) pour prouver le câblage
    `generate_mission_response`, jamais présenté comme une preuve
    d'inférence réelle."""

    def __init__(self, *, response_text: str = "def factorial(n):\n    return 1 if n <= 1 else n * factorial(n - 1)\n") -> None:
        self.response_text = response_text
        self.should_fail = False
        self.received_requests: list[GenerationRequest] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="fake-test-double",
            detail="double de test — jamais un vrai backend",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.received_requests.append(request)
        if self.should_fail:
            raise ModelProviderError(
                "FakeModelProvider.generate", endpoint_url=request.endpoint.url, detail="échec simulé pour test",
            )
        return GenerationResult(
            text=self.response_text, model_used=request.endpoint.served_model,
            tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
            finish_reason="stop", elapsed_s=0.001, facts={"fake": True},
        )


class TestGenerateMissionResponseWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.mission_service = MissionService(self.factory)
        self.provider = _FakeModelProvider()
        self.endpoint = an_endpoint()
        self.mission = self.mission_service.create(
            title="Factorielle", description="d", specialty="research",
            objective="Écris une fonction Python qui calcule la factorielle.",
            created_by=Subject.HUMAN,
        )

    def test_full_chain_reaches_review_with_the_generated_text(self) -> None:
        self.assertEqual(self.mission.state, MissionState.QUEUED)
        final = generate_mission_response(
            self.mission.id, mission_service=self.mission_service,
            endpoint=self.endpoint, model_provider=self.provider,
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertEqual(final.result, self.provider.response_text)

    def test_never_jumps_directly_to_completed(self) -> None:
        final = generate_mission_response(
            self.mission.id, mission_service=self.mission_service,
            endpoint=self.endpoint, model_provider=self.provider,
        )
        self.assertNotEqual(final.state, MissionState.COMPLETED)
        self.assertEqual(final.state, MissionState.REVIEW)

    def test_mission_objective_is_used_as_the_default_prompt(self) -> None:
        generate_mission_response(
            self.mission.id, mission_service=self.mission_service,
            endpoint=self.endpoint, model_provider=self.provider,
        )
        self.assertEqual(len(self.provider.received_requests), 1)
        self.assertEqual(self.provider.received_requests[0].prompt, self.mission.objective)

    def test_an_explicit_prompt_overrides_the_objective(self) -> None:
        generate_mission_response(
            self.mission.id, mission_service=self.mission_service,
            endpoint=self.endpoint, model_provider=self.provider, prompt="prompt explicite",
        )
        self.assertEqual(self.provider.received_requests[0].prompt, "prompt explicite")

    def test_a_provider_failure_marks_the_mission_failed_never_silently_swallowed(self) -> None:
        self.provider.should_fail = True
        final = generate_mission_response(
            self.mission.id, mission_service=self.mission_service,
            endpoint=self.endpoint, model_provider=self.provider,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertIsNotNone(final.error)
        self.assertIsNone(final.result)

    def test_a_clone_subject_cannot_trigger_generation(self) -> None:
        """`mark_running` exige déjà `Action.LAUNCH_MISSION` (CORE seul,
        `apex.policy.engine`) — aucune règle dupliquée dans
        `generate_mission_response`."""
        with self.assertRaises(PolicyDenied):
            generate_mission_response(
                self.mission.id, mission_service=self.mission_service,
                endpoint=self.endpoint, model_provider=self.provider, requested_by=Subject.RESEARCH,
            )

    def test_unknown_mission_raises(self) -> None:
        with self.assertRaises(LookupError):
            generate_mission_response(
                "does-not-exist", mission_service=self.mission_service,
                endpoint=self.endpoint, model_provider=self.provider,
            )


def _real_ollama_url() -> str | None:
    """Constat réel — jamais supposé. Lit `APEX_OLLAMA_URL` si fourni
    (pour pointer vers l'Ollama réel de l'utilisateur, pas forcément
    local à cette machine), sinon vérifie le port par défaut en local —
    même patron que `tests/test_models_ollama.py::_real_ollama_url`."""
    configured = os.environ.get("APEX_OLLAMA_URL")
    if configured:
        return configured
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return "http://127.0.0.1:11434"
    except OSError:
        return None


@unittest.skipUnless(_real_ollama_url() is not None, "aucun Ollama réel joignable (définir APEX_OLLAMA_URL pour pointer vers le vôtre)")
class TestRealOllamaMissionIntegration(unittest.TestCase):
    """Le SEUL test de ce fichier contre un VRAI Ollama — s'exécute
    automatiquement dès qu'un endpoint réel est joignable (local ou via
    `APEX_OLLAMA_URL`), sans aucune modification de code. Sauté
    explicitement sinon, jamais remplacé par une simulation."""

    def test_a_real_mission_gets_a_real_model_response(self) -> None:
        url = _real_ollama_url()
        assert url is not None
        served_model = os.environ.get("APEX_OLLAMA_MODEL", "qwen3-coder:30b")
        endpoint = an_endpoint(url=url, served_model=served_model)

        factory = sqlite_session_factory()
        mission_service = MissionService(factory)
        mission = mission_service.create(
            title="Factorielle", description="test d'intégration réel", specialty="research",
            objective="Écris une fonction Python qui calcule la factorielle.",
            created_by=Subject.HUMAN,
        )
        final = generate_mission_response(mission.id, mission_service=mission_service, endpoint=endpoint)
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertIsNotNone(final.result)
        self.assertGreater(len(final.result.strip()), 0)


if __name__ == "__main__":
    unittest.main()
