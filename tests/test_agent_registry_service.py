"""Tests d'`AgentRegistryService` — SQLite en mémoire, même patron que
`tests/test_conversations_service.py`. Le chemin réel Agent -> Mission ->
Model Router -> provider est prouvé contre un double de test fidèle au
contrat `ModelProvider` (jamais un vrai modèle) — mêmes garanties que
`tests/test_missions_router_generation.py`.
"""

from __future__ import annotations

import copy
import unittest

from sqlalchemy import create_engine

from apex.agent_registry.fallback import classify_outcome
from apex.agent_registry.model import (
    AgentExecutionStatus,
    AgentStatus,
    FallbackReason,
    Permission,
    ToolName,
    derive_agent_status,
)
from apex.agent_registry.service import AgentRegistryService
from apex.agent_templates.model import AgentRole
from apex.agent_templates.service import AgentTemplateService
from apex.config.loader import parse_config
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError, PolicyDenied
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject
from apex.routing.model import Capability


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


BASE_CONFIG: dict = {
    "version": 1,
    "phase": 1,
    "paths": {
        "data_root": "/home/apex/data", "workspaces": "/home/apex/data/workspaces",
        "artifacts": "/home/apex/data/artifacts", "logs": "/home/apex/data/logs",
    },
    "compute": {
        "pools": {
            "local-gpu": {
                "location": "local", "trust_level": "trusted",
                "capabilities": ["inference", "sandbox"],
                "gpus": [{"model": "RTX 4060 Ti", "vram_gb": 8}],
                "cpu_cores": 16, "ram_gb": 8,
            }
        }
    },
    "models": {
        "roles": {
            "fast": {
                "candidates": [
                    {"family": "qwen2.5-coder", "version": "7b-instruct",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
            "judge": {
                "candidates": [
                    {"family": "gemma4", "version": "12b",
                     "quantization": "q4_k_m", "capability_class": "judge"},
                ],
            },
        },
        "endpoints": [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen2.5-coder:7b-instruct", "max_context": 32768, "vram_gb": 5.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "gemma4:12b-q4_K_M", "max_context": 16384, "vram_gb": 8.0},
        ],
    },
    "external": {"enabled": False, "allowed_contexts": ["interactive", "research"]},
    "budget": {"nightly_gpu_minutes": 600, "max_experiment_gpu_minutes": 240},
    "sandbox": {"provider": "docker", "network": "none"},
    "languages": {"python": {"wave": 1, "image": "python:3.12-slim", "oracle_strength": "strong"}},
    "services": {"postgres": {"host": "127.0.0.1", "port": 5432}},
}


def a_config():
    return parse_config(copy.deepcopy(BASE_CONFIG))


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


class TestAgentLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)

    def test_human_can_create_an_agent(self) -> None:
        agent = self.service.create(name="APEX Coder", role=AgentRole.CODER, created_by=Subject.HUMAN)
        self.assertEqual(agent.role, AgentRole.CODER)
        self.assertTrue(agent.enabled)

    def test_no_tool_or_permission_is_granted_by_default(self) -> None:
        """§6/§7 de la directive : jamais accordé automatiquement."""
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        self.assertEqual(agent.tools, ())
        self.assertEqual(agent.permissions, ())

    def test_a_clone_cannot_create_an_agent(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(name="x", role=AgentRole.CODER, created_by=Subject.DEVELOPER)

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(name="  ", role=AgentRole.CODER, created_by=Subject.HUMAN)

    def test_unknown_required_capability_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(
                name="n", role=AgentRole.CODER, created_by=Subject.HUMAN,
                required_capabilities=("not-a-real-capability",),
            )

    def test_declared_capabilities_are_stored_and_reused_by_the_router(self) -> None:
        agent = self.service.create(
            name="n", role=AgentRole.CODER, created_by=Subject.HUMAN,
            required_capabilities=(Capability.CODING.value,),
        )
        self.assertIn(Capability.CODING.value, agent.required_capabilities)

    def test_declared_tools_and_permissions_are_stored_verbatim(self) -> None:
        agent = self.service.create(
            name="n", role=AgentRole.CODER, created_by=Subject.HUMAN,
            tools=(ToolName.FILES.value, ToolName.TERMINAL.value),
            permissions=(Permission.EXECUTE.value, Permission.WORKSPACE_WRITE.value),
        )
        self.assertEqual(agent.tools, (ToolName.FILES, ToolName.TERMINAL))
        self.assertEqual(agent.permissions, (Permission.EXECUTE, Permission.WORKSPACE_WRITE))

    def test_created_agent_is_retrievable_and_listed(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.RESEARCHER, created_by=Subject.HUMAN)
        self.assertEqual(self.service.get(agent.id).id, agent.id)
        self.assertIn(agent.id, [a.id for a in self.service.list_all()])

    def test_human_can_update_an_agent(self) -> None:
        agent = self.service.create(name="old", role=AgentRole.CODER, created_by=Subject.HUMAN)
        updated = self.service.update(agent.id, requested_by=Subject.HUMAN, name="new")
        self.assertEqual(updated.name, "new")

    def test_a_clone_cannot_update_an_agent(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.service.update(agent.id, requested_by=Subject.SECURITY, name="x")

    def test_human_can_delete_an_agent(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        self.assertTrue(self.service.delete(agent.id, requested_by=Subject.HUMAN))
        self.assertIsNone(self.service.get(agent.id))

    def test_a_clone_cannot_delete_an_agent(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.service.delete(agent.id, requested_by=Subject.CRITIC)


class TestAgentTemplateToAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)
        self.templates = AgentTemplateService(self.factory)

    def test_instantiating_a_template_copies_its_configuration(self) -> None:
        template = self.templates.create(
            name="CODER TEMPLATE", role=AgentRole.CODER, description="modèle de base",
            created_by=Subject.HUMAN, model_role="fast",
        )
        agent = self.service.instantiate_from_template(
            template.id, name="APEX Backend Coder", created_by=Subject.HUMAN,
        )
        self.assertEqual(agent.role, AgentRole.CODER)
        self.assertEqual(agent.model_role, "fast")
        self.assertEqual(agent.template_id, template.id)
        self.assertEqual(agent.name, "APEX Backend Coder")

    def test_instantiating_an_unknown_template_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.instantiate_from_template("does-not-exist", name="x", created_by=Subject.HUMAN)


class TestAgentStatusDerivation(unittest.TestCase):
    """`AgentStatus` n'est jamais stocké — toujours dérivé."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)

    def test_disabled_agent_is_disabled_regardless_of_execution_history(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN, enabled=False)
        self.assertEqual(derive_agent_status(agent, None), AgentStatus.DISABLED)

    def test_enabled_agent_with_no_execution_is_idle(self) -> None:
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        self.assertEqual(derive_agent_status(agent, None), AgentStatus.IDLE)

    def test_never_fabricates_waiting(self) -> None:
        """§4 de la directive : ne jamais créer un état fictif — `WAITING`
        n'est produit par aucune source réelle aujourd'hui."""
        agent = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN)
        for status in AgentExecutionStatus:
            from apex.agent_registry.model import AgentExecution
            from datetime import datetime, timezone

            execution = AgentExecution(
                id="x", agent_id=agent.id, mission_id="m", status=status,
                model_id=None, provider_id=None, result=None, error=None, fallback_reason=None,
                started_at=datetime.now(timezone.utc), completed_at=None,
            )
            self.assertNotEqual(derive_agent_status(agent, execution), AgentStatus.WAITING)


class TestAgentTeam(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)

    def test_human_can_create_a_team_of_existing_agents(self) -> None:
        a1 = self.service.create(name="a1", role=AgentRole.CODER, created_by=Subject.HUMAN)
        a2 = self.service.create(name="a2", role=AgentRole.TESTER, created_by=Subject.HUMAN)
        team = self.service.create_team(
            name="Dev Team", description="d", agent_ids=(a1.id, a2.id), created_by=Subject.HUMAN,
        )
        self.assertEqual(set(team.agent_ids), {a1.id, a2.id})
        self.assertIn(team.id, [t.id for t in self.service.list_teams()])

    def test_team_with_unknown_agent_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create_team(
                name="x", description="", agent_ids=("does-not-exist",), created_by=Subject.HUMAN,
            )

    def test_a_clone_cannot_create_a_team(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create_team(name="x", description="", agent_ids=(), created_by=Subject.BENCHMARK)


class TestRunExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentRegistryService(self.factory)
        self.mission_service = MissionService(self.factory)
        self.agent = self.service.create(
            name="APEX Coder", role=AgentRole.CODER, created_by=Subject.HUMAN,
            model_role="fast", permissions=(Permission.EXECUTE.value,),
        )
        self.mission = self.mission_service.create(
            title="t", description="d", specialty="research",
            objective="Écris une fonction Python qui calcule la factorielle.",
            created_by=Subject.HUMAN, agent_id=self.agent.id,
        )

    def test_full_chain_agent_to_mission_to_router_to_provider(self) -> None:
        provider = _FakeProvider(text="réponse réelle via agent")
        execution = self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: provider,
        )
        self.assertEqual(execution.status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(execution.result, "réponse réelle via agent")
        self.assertEqual(execution.model_id, "qwen2.5-coder:7b-instruct:q4_k_m")
        self.assertEqual(execution.provider_id, "ollama:http://127.0.0.1:11434")
        self.assertEqual(execution.fallback_reason, FallbackReason.SUCCESS)

        final_mission = self.mission_service.get(self.mission.id)
        self.assertEqual(final_mission.state, MissionState.REVIEW)
        self.assertEqual(final_mission.result, "réponse réelle via agent")

    def test_disabled_agent_is_never_executable(self) -> None:
        self.service.update(self.agent.id, requested_by=Subject.HUMAN, enabled=False)
        with self.assertRaises(ValueError):
            self.service.run_execution(
                self.agent.id, self.mission.id, mission_service=self.mission_service, config=a_config(),
            )

    def test_agent_without_execute_permission_is_refused(self) -> None:
        no_exec = self.service.create(name="n", role=AgentRole.CODER, created_by=Subject.HUMAN, model_role="fast")
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, agent_id=no_exec.id,
        )
        with self.assertRaises(ValueError):
            self.service.run_execution(no_exec.id, mission.id, mission_service=self.mission_service, config=a_config())

    def test_a_clone_cannot_trigger_an_execution(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.run_execution(
                self.agent.id, self.mission.id, mission_service=self.mission_service,
                config=a_config(), requested_by=Subject.RESEARCH,
            )

    def test_provider_failure_yields_failed_execution_and_failed_mission(self) -> None:
        failing = _FakeProvider(mode="provider_error")
        execution = self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: failing,
        )
        self.assertEqual(execution.status, AgentExecutionStatus.FAILED)
        self.assertIn("tous les candidats ont échoué", execution.error)
        self.assertEqual(execution.fallback_reason, FallbackReason.PROVIDER_ERROR)

        final_mission = self.mission_service.get(self.mission.id)
        self.assertEqual(final_mission.state, MissionState.FAILED)

    def test_no_matching_capability_yields_failed_execution_never_a_fake_response(self) -> None:
        picky = self.service.create(
            name="picky", role=AgentRole.CODER, created_by=Subject.HUMAN, model_role="fast",
            permissions=(Permission.EXECUTE.value,), required_capabilities=(Capability.VISION.value,),
        )
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, agent_id=picky.id,
        )
        execution = self.service.run_execution(
            picky.id, mission.id, mission_service=self.mission_service, config=a_config(),
        )
        self.assertEqual(execution.status, AgentExecutionStatus.FAILED)
        self.assertIsNone(execution.result)
        self.assertEqual(execution.fallback_reason, FallbackReason.CAPABILITY_MISSING)

    def test_execution_is_listed_by_agent_and_by_mission(self) -> None:
        provider = _FakeProvider()
        execution = self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: provider,
        )
        self.assertIn(execution.id, [e.id for e in self.service.list_executions(self.agent.id)])
        self.assertIn(execution.id, [e.id for e in self.service.list_executions_for_mission(self.mission.id)])
        self.assertEqual(self.service.latest_execution(self.agent.id).id, execution.id)

    def test_agent_status_reflects_the_latest_execution(self) -> None:
        provider = _FakeProvider()
        self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: provider,
        )
        latest = self.service.latest_execution(self.agent.id)
        status = derive_agent_status(self.service.get(self.agent.id), latest)
        self.assertEqual(status, AgentStatus.COMPLETED)

    def test_never_fabricates_token_usage_or_cost(self) -> None:
        """§28 de la directive — aucun champ de coût/tokens n'existe sur
        `AgentExecution` faute d'être réellement fourni par les providers
        actuels (voir `apex.models.model.GenerationResult`)."""
        provider = _FakeProvider()
        execution = self.service.run_execution(
            self.agent.id, self.mission.id, mission_service=self.mission_service,
            config=a_config(), provider_factory=lambda c: provider,
        )
        self.assertFalse(hasattr(execution, "token_usage"))
        self.assertFalse(hasattr(execution, "cost"))


class TestClassifyOutcome(unittest.TestCase):
    def test_success_when_a_result_exists(self) -> None:
        from apex.routing.generation import FallbackResult
        from apex.routing.model import RoutingDecision

        decision = RoutingDecision(selected=object(), reason="ok", ranked_candidates=())
        result = FallbackResult(result=object(), succeeded_with=object(), attempts=())
        self.assertEqual(classify_outcome(decision, result), FallbackReason.SUCCESS)

    def test_capability_missing_when_no_candidate_selected(self) -> None:
        from apex.routing.model import RoutingDecision

        decision = RoutingDecision(selected=None, reason="capacités manquantes : ['vision']", ranked_candidates=())
        self.assertEqual(classify_outcome(decision, None), FallbackReason.CAPABILITY_MISSING)

    def test_never_produces_quota_or_auth_reasons_no_real_signal_exists(self) -> None:
        """§24/§28 de la directive : ces valeurs restent dans le vocabulaire
        fermé mais ne sont jamais produites faute de signal réel."""
        from apex.routing.generation import GenerationAttempt
        from apex.routing.model import RoutingDecision

        decision = RoutingDecision(selected=object(), reason="ok", ranked_candidates=())

        class _FakeResult:
            result = None
            attempts = (GenerationAttempt(candidate=object(), succeeded=False, error="connexion refusée", result=None),)

        outcome = classify_outcome(decision, _FakeResult())
        self.assertNotIn(outcome, (FallbackReason.QUOTA_EXCEEDED, FallbackReason.RATE_LIMITED,
                                    FallbackReason.AUTH_ERROR, FallbackReason.OFFLINE,
                                    FallbackReason.CONTEXT_TOO_LONG))


if __name__ == "__main__":
    unittest.main()
