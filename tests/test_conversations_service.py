"""Tests de `ConversationService` — Chat réel, SQLite en mémoire, aucune
génération simulée présentée comme réelle : `_FakeProvider` implémente
fidèlement le contrat `ModelProvider` (même patron que
`tests/test_missions_router_generation.py`), jamais un texte codé en dur
retourné sans passer par `select_model`/`generate_with_fallback`.
"""

from __future__ import annotations

import copy
import unittest

from sqlalchemy import create_engine

from apex.config.loader import parse_config
from apex.config.schema import ModelEndpoint, ModelRef
from apex.conversations.model import MessageRole, MessageStatus
from apex.conversations.service import ConversationService
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError, PolicyDenied
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject


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


class TestConversationLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = ConversationService(self.factory)

    def test_human_can_create_a_conversation(self) -> None:
        conversation = self.service.create(title="Ma conversation", created_by=Subject.HUMAN)
        self.assertEqual(conversation.title, "Ma conversation")
        self.assertIsNone(conversation.project_id)

    def test_a_clone_cannot_create_a_conversation(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(title="x", created_by=Subject.RESEARCH)

    def test_core_cannot_create_a_conversation(self) -> None:
        """Le Chat est une action humaine directe — jamais une automatisation
        déclenchée par le CORE (contrairement à LAUNCH_MISSION/CANCEL_MISSION)."""
        with self.assertRaises(PolicyDenied):
            self.service.create(title="x", created_by=Subject.CORE)

    def test_empty_title_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(title="   ", created_by=Subject.HUMAN)

    def test_created_conversation_is_listed(self) -> None:
        conversation = self.service.create(title="c1", created_by=Subject.HUMAN)
        listed = self.service.list_all()
        self.assertIn(conversation.id, [c.id for c in listed])

    def test_rename_updates_title(self) -> None:
        conversation = self.service.create(title="ancien", created_by=Subject.HUMAN)
        renamed = self.service.rename(conversation.id, title="nouveau", requested_by=Subject.HUMAN)
        self.assertEqual(renamed.title, "nouveau")

    def test_rename_unknown_conversation_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.rename("does-not-exist", title="x", requested_by=Subject.HUMAN)

    def test_a_clone_cannot_rename(self) -> None:
        conversation = self.service.create(title="c", created_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.service.rename(conversation.id, title="x", requested_by=Subject.DEVELOPER)

    def test_delete_removes_the_conversation(self) -> None:
        conversation = self.service.create(title="c", created_by=Subject.HUMAN)
        deleted = self.service.delete(conversation.id, requested_by=Subject.HUMAN)
        self.assertTrue(deleted)
        self.assertIsNone(self.service.get(conversation.id))

    def test_delete_unknown_conversation_returns_false(self) -> None:
        self.assertFalse(self.service.delete("does-not-exist", requested_by=Subject.HUMAN))

    def test_a_clone_cannot_delete(self) -> None:
        conversation = self.service.create(title="c", created_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.service.delete(conversation.id, requested_by=Subject.SECURITY)

    def test_project_id_is_stored_when_provided(self) -> None:
        conversation = self.service.create(title="c", created_by=Subject.HUMAN, project_id="proj-1")
        self.assertEqual(conversation.project_id, "proj-1")


class TestSendMessage(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = ConversationService(self.factory)
        self.conversation = self.service.create(title="c", created_by=Subject.HUMAN)

    def test_a_clone_cannot_send_a_message(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.send_message(
                self.conversation.id, content="bonjour", created_by=Subject.RESEARCH, config=a_config(),
            )

    def test_unknown_conversation_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.send_message(
                "does-not-exist", content="bonjour", created_by=Subject.HUMAN, config=a_config(),
            )

    def test_empty_content_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.send_message(
                self.conversation.id, content="   ", created_by=Subject.HUMAN, config=a_config(),
            )

    def test_full_chain_selects_and_generates_a_real_response(self) -> None:
        """UI -> service -> select_model -> generate_with_fallback ->
        provider (double fidèle) -> réponse réelle, persistée."""
        provider = _FakeProvider(text="réponse réelle du chat")
        user_msg, assistant_msg = self.service.send_message(
            self.conversation.id, content="explique-moi X", created_by=Subject.HUMAN,
            config=a_config(), model_role="fast", provider_factory=lambda c: provider,
        )
        self.assertEqual(user_msg.role, MessageRole.USER)
        self.assertEqual(user_msg.content, "explique-moi X")
        self.assertEqual(user_msg.status, MessageStatus.REAL)
        self.assertEqual(assistant_msg.role, MessageRole.ASSISTANT)
        self.assertEqual(assistant_msg.content, "réponse réelle du chat")
        self.assertEqual(assistant_msg.status, MessageStatus.REAL)
        self.assertEqual(assistant_msg.model_id, "qwen2.5-coder:7b-instruct:q4_k_m")
        self.assertEqual(assistant_msg.provider_id, "ollama:http://127.0.0.1:11434")
        self.assertEqual(len(provider.received_requests), 1)
        self.assertEqual(provider.received_requests[0].prompt, "explique-moi X")

    def test_both_messages_are_persisted_and_listed_in_order(self) -> None:
        provider = _FakeProvider(text="ok")
        self.service.send_message(
            self.conversation.id, content="premier message", created_by=Subject.HUMAN,
            config=a_config(), model_role="fast", provider_factory=lambda c: provider,
        )
        messages = self.service.list_messages(self.conversation.id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, MessageRole.USER)
        self.assertEqual(messages[1].role, MessageRole.ASSISTANT)

    def test_no_matching_role_yields_error_message_never_a_fake_response(self) -> None:
        user_msg, assistant_msg = self.service.send_message(
            self.conversation.id, content="x", created_by=Subject.HUMAN,
            config=a_config(), model_role="does-not-exist",
        )
        self.assertEqual(assistant_msg.status, MessageStatus.ERROR)
        self.assertEqual(assistant_msg.content, "")
        self.assertIn("aucun modèle disponible", assistant_msg.error)

    def test_provider_failure_yields_error_message_with_detail_never_masked(self) -> None:
        failing = _FakeProvider(mode="provider_error")
        user_msg, assistant_msg = self.service.send_message(
            self.conversation.id, content="x", created_by=Subject.HUMAN,
            config=a_config(), model_role="fast", provider_factory=lambda c: failing,
        )
        self.assertEqual(assistant_msg.status, MessageStatus.ERROR)
        self.assertIn("tous les candidats ont échoué", assistant_msg.error)

    def test_sending_a_message_advances_conversation_updated_at(self) -> None:
        before = self.service.get(self.conversation.id).updated_at
        provider = _FakeProvider(text="ok")
        self.service.send_message(
            self.conversation.id, content="x", created_by=Subject.HUMAN,
            config=a_config(), model_role="fast", provider_factory=lambda c: provider,
        )
        after = self.service.get(self.conversation.id).updated_at
        self.assertGreaterEqual(after, before)

    def test_without_model_role_all_candidates_are_considered(self) -> None:
        """Sans rôle explicite, `select_model` reste déterministe — tri
        alphabétique (role, family, version), jamais un choix devine."""
        provider = _FakeProvider(text="ok")
        user_msg, assistant_msg = self.service.send_message(
            self.conversation.id, content="x", created_by=Subject.HUMAN,
            config=a_config(), provider_factory=lambda c: provider,
        )
        self.assertEqual(assistant_msg.status, MessageStatus.REAL)
        # "fast" précède "judge" alphabétiquement — sélection déterministe,
        # jamais aléatoire ni dépendante de l'ordre d'itération d'un dict.
        self.assertEqual(assistant_msg.model_id, "qwen2.5-coder:7b-instruct:q4_k_m")


if __name__ == "__main__":
    unittest.main()


class TestConversationMemory(unittest.TestCase):
    """Le modèle reçoit les échanges RÉELS précédents (bornés) — avant le 05/10/2026, seul le dernier message
    lui était envoyé : la conversation n'avait aucune mémoire."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = ConversationService(self.factory)
        self.conversation = self.service.create(title="mémoire", created_by=Subject.HUMAN)

    def _send(self, content: str, provider: _FakeProvider):
        return self.service.send_message(
            self.conversation.id, content=content, created_by=Subject.HUMAN, config=a_config(),
            model_role="fast", provider_factory=lambda c: provider,
        )

    def test_the_second_message_carries_the_first_exchange(self) -> None:
        provider = _FakeProvider(text="Bonjour Alice.")
        self._send("Je m'appelle Alice.", provider)
        self._send("Comment je m'appelle ?", provider)
        prompt = provider.received_requests[1].prompt
        self.assertIn("Utilisateur : Je m'appelle Alice.", prompt)
        self.assertIn("Assistant : Bonjour Alice.", prompt)
        self.assertTrue(prompt.endswith("Nouveau message de l'utilisateur :\nComment je m'appelle ?"))
        self.assertLess(prompt.index("Utilisateur : Je m'appelle"), prompt.index("Assistant : Bonjour"))

    def test_failed_assistant_messages_are_never_replayed_as_if_real(self) -> None:
        self._send("question 1", _FakeProvider(mode="provider_error"))
        provider = _FakeProvider(text="ok")
        self._send("question 2", provider)
        prompt = provider.received_requests[0].prompt
        self.assertIn("Utilisateur : question 1", prompt)
        self.assertNotIn("Assistant :", prompt)  # la réponse en erreur n'existe pas pour le modèle

    def test_history_is_bounded_and_keeps_the_most_recent_exchanges(self) -> None:
        from apex.conversations.service import MAX_HISTORY_CHARS

        provider = _FakeProvider(text="r" * 1500)
        for i in range(12):
            self._send(f"message numéro {i:02d} " + "x" * 1500, provider)
        prompt = provider.received_requests[-1].prompt
        self.assertIn("message numéro 10", prompt)
        self.assertNotIn("message numéro 00", prompt)
        self.assertLess(len(prompt), MAX_HISTORY_CHARS + 2000)

    def test_conversations_never_share_memory(self) -> None:
        other = self.service.create(title="autre", created_by=Subject.HUMAN)
        self._send("secret de la première conversation", _FakeProvider(text="ok"))
        provider = _FakeProvider(text="ok")
        self.service.send_message(
            other.id, content="bonjour", created_by=Subject.HUMAN, config=a_config(), model_role="fast",
            provider_factory=lambda c: provider,
        )
        self.assertEqual(provider.received_requests[0].prompt, "bonjour")
