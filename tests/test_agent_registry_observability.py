"""Tests d'`apex.agent_registry.observability` (sérialisation) et de
`AgentRegistryService.get_execution_trace` (passe « Routing & Execution
Observability Hardening », N+18) — vérifie que la décision de routage
(candidat sélectionné, score, `ScoreBreakdown`, alternatives écartées) et
les tentatives de fallback (catégorie réelle par tentative) sont
réellement reconstructibles APRÈS COUP depuis `events`, jamais un second
stockage, et que le texte généré/le prompt ne fuient jamais dans la trace.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agent_registry.model import Permission
from apex.agent_registry.observability import serialize_fallback_result, serialize_routing_decision
from apex.agent_registry.service import AgentRegistryService
from apex.agent_templates.model import AgentRole
from apex.config.loader import parse_config
from apex.config.schema import ModelEndpoint, ModelRef
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelErrorCategory, ModelProviderError
from apex.missions.service import MissionService
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate
from apex.obs.capability import CapabilityStatus
from apex.obs.status import DataStatus, Measurement
from apex.policy.model import Subject
from apex.routing.generation import FallbackResult, GenerationAttempt
from apex.routing.model import RoutingDecision, ScoreBreakdown, ScoreContribution, ScoredCandidate


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_candidate(model_id: str = "qwen2.5-coder:7b-instruct:q4_k_m") -> ModelCandidate:
    family, version, quantization = model_id.split(":")
    ref = ModelRef(family=family, version=version, quantization=quantization, capability_class="coder")
    endpoint = ModelEndpoint(
        pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
        served_model=f"{family}:{version}", max_context=32768,
    )
    return ModelCandidate(role="coder_main", ref=ref, resident=False, endpoint=endpoint)


BASE_CONFIG: dict = {
    "version": 1, "phase": 1,
    "paths": {
        "data_root": "/home/apex/data", "workspaces": "/home/apex/data/workspaces",
        "artifacts": "/home/apex/data/artifacts", "logs": "/home/apex/data/logs",
    },
    "compute": {
        "pools": {
            "local-gpu": {
                "location": "local", "trust_level": "trusted", "capabilities": ["inference", "sandbox"],
                "gpus": [{"model": "RTX 4060 Ti", "vram_gb": 8}], "cpu_cores": 16, "ram_gb": 8,
            }
        }
    },
    "models": {
        "roles": {
            "fast": {
                "candidates": [
                    {"family": "qwen2.5-coder", "version": "7b-instruct", "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
        },
        "endpoints": [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen2.5-coder:7b-instruct", "max_context": 32768},
        ],
    },
    "external": {"enabled": False},
    "sandbox": {"level": "namespaced"},
    "gate": {"required": []},
}


def a_config():
    import copy

    return parse_config(copy.deepcopy(BASE_CONFIG))


class _FailingProvider(ModelProvider):
    def __init__(self, category: ModelErrorCategory) -> None:
        self.category = category

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="fake", detail="double de test")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ModelProviderError(
            "FailingProvider.generate", endpoint_url=request.endpoint.url, detail="échec simulé",
            category=self.category,
        )


class TestSerializeRoutingDecision(unittest.TestCase):
    def test_serializes_the_selected_candidate_and_all_alternatives(self) -> None:
        selected = a_candidate("qwen2.5-coder:7b-instruct:q4_k_m")
        rejected = a_candidate("phi3:mini:q4_k_m")
        breakdown = ScoreBreakdown(
            contributions=(ScoreContribution(name="capability_match", value=1.0, status=DataStatus.REAL, reason="ok"),),
        )
        decision = RoutingDecision(
            selected=selected, reason="meilleur score",
            ranked_candidates=(
                ScoredCandidate(candidate=selected, score=1.5, reasons=("ok",), breakdown=breakdown),
                ScoredCandidate(candidate=rejected, score=0.0, reasons=("exclu : capacités manquantes",)),
            ),
        )
        payload = serialize_routing_decision(decision)
        self.assertEqual(payload["selected_model_id"], selected.ref.id)
        self.assertEqual(payload["reason"], "meilleur score")
        model_ids = {c["model_id"] for c in payload["candidates"]}
        self.assertEqual(model_ids, {selected.ref.id, rejected.ref.id})
        rejected_entry = next(c for c in payload["candidates"] if c["model_id"] == rejected.ref.id)
        self.assertEqual(rejected_entry["score"], 0.0)
        self.assertIn("exclu : capacités manquantes", rejected_entry["reasons"])

    def test_no_selection_serializes_a_null_model_id(self) -> None:
        decision = RoutingDecision(selected=None, reason="aucun candidat éligible", ranked_candidates=())
        payload = serialize_routing_decision(decision)
        self.assertIsNone(payload["selected_model_id"])
        self.assertEqual(payload["candidates"], [])


class TestSerializeFallbackResult(unittest.TestCase):
    def test_none_result_serializes_to_empty_attempts(self) -> None:
        payload = serialize_fallback_result(None)
        self.assertEqual(payload, {"attempts": [], "succeeded_with_model_id": None})

    def test_records_the_real_category_of_each_failed_attempt(self) -> None:
        candidate = a_candidate()
        attempt = GenerationAttempt(
            candidate=candidate, succeeded=False, error="HTTP 429", result=None,
            category=ModelErrorCategory.RATE_LIMITED,
        )
        result = FallbackResult(result=None, succeeded_with=None, attempts=(attempt,))
        payload = serialize_fallback_result(result)
        self.assertEqual(len(payload["attempts"]), 1)
        self.assertEqual(payload["attempts"][0]["category"], "rate_limited")
        self.assertFalse(payload["attempts"][0]["succeeded"])
        self.assertIsNone(payload["succeeded_with_model_id"])

    def test_never_leaks_generated_text_only_metadata(self) -> None:
        candidate = a_candidate()
        generation = GenerationResult(
            text="CONTENU SENSIBLE DU PROMPT/RÉPONSE", model_used="qwen2.5-coder:7b-instruct",
            tokens_prompt=Measurement.real(5), tokens_completion=Measurement.real(7),
            finish_reason="stop", elapsed_s=0.5, facts={},
        )
        attempt = GenerationAttempt(candidate=candidate, succeeded=True, error=None, result=generation, category=None)
        result = FallbackResult(result=generation, succeeded_with=candidate, attempts=(attempt,))
        payload = serialize_fallback_result(result)
        serialized_text = str(payload)
        self.assertNotIn("CONTENU SENSIBLE", serialized_text)
        self.assertEqual(payload["attempts"][0]["tokens_prompt"], 5)
        self.assertEqual(payload["attempts"][0]["tokens_completion"], 7)
        self.assertEqual(payload["succeeded_with_model_id"], candidate.ref.id)


class TestGetExecutionTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)
        self.mission_service = MissionService(self.factory)
        self.agent = self.service.create(
            name="APEX Coder", role=AgentRole.CODER, created_by=Subject.HUMAN,
            model_role="fast", permissions=(Permission.EXECUTE.value,),
        )
        self.mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, agent_id=self.agent.id,
        )

    def test_trace_reconstructs_the_routing_decision_and_the_real_failure_category(self) -> None:
        failing = _FailingProvider(ModelErrorCategory.AUTH_ERROR)
        execution = self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: failing,
        )
        trace = self.service.get_execution_trace(execution.id)
        self.assertEqual(trace["execution_id"], execution.id)
        self.assertIsNotNone(trace["routing"])
        self.assertIsNotNone(trace["routing"]["selected_model_id"])
        self.assertIsNotNone(trace["fallback"])
        self.assertEqual(len(trace["fallback"]["attempts"]), 1)
        self.assertEqual(trace["fallback"]["attempts"][0]["category"], "auth_error")
        self.assertEqual(trace["completion_kind"], "agent_execution.failed")

    def test_trace_for_an_unknown_execution_id_is_empty(self) -> None:
        trace = self.service.get_execution_trace("does-not-exist")
        self.assertIsNone(trace["routing"])
        self.assertIsNone(trace["fallback"])


if __name__ == "__main__":
    unittest.main()
