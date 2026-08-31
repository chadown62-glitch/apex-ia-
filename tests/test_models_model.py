"""Tests de apex.models.model — dataclasses pures, aucun réseau."""

from __future__ import annotations

import threading
import unittest

from apex.config.schema import ModelEndpoint
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.obs.capability import CapabilityStatus
from apex.obs.status import DataStatus, Measurement


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="qwen3-coder:30b", max_context=8192,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class TestGenerationRequest(unittest.TestCase):
    def test_construction_requires_no_hardcoded_model(self) -> None:
        """Aucun champ modèle par défaut — l'endpoint vient toujours de l'appelant."""
        fields = GenerationRequest.__dataclass_fields__
        self.assertIn("endpoint", fields)
        self.assertNotIn("model", fields)
        self.assertNotIn("served_model", fields)

    def test_timeout_has_a_default_never_unbounded(self) -> None:
        req = GenerationRequest(endpoint=an_endpoint(), prompt="x")
        self.assertGreater(req.timeout_s, 0)

    def test_cancel_event_is_a_plain_threading_event_not_sandbox_token(self) -> None:
        """Décision explicite Phase 6 : pas de réutilisation de
        apex.sandbox.model.CancellationToken — un threading.Event coopératif,
        contrat indépendant."""
        event = threading.Event()
        req = GenerationRequest(endpoint=an_endpoint(), prompt="x", cancel_event=event)
        self.assertIs(req.cancel_event, event)
        from apex.sandbox.model import CancellationToken

        self.assertNotIsInstance(req.cancel_event, CancellationToken)


class TestGenerationResult(unittest.TestCase):
    def test_tokens_are_measurements_not_bare_ints(self) -> None:
        result = GenerationResult(
            text="ok", model_used="qwen3-coder:30b",
            tokens_prompt=Measurement.real(12), tokens_completion=Measurement.unknown(),
            finish_reason="stop", elapsed_s=0.1,
        )
        self.assertEqual(result.tokens_prompt.status, DataStatus.REAL)
        self.assertEqual(result.tokens_completion.status, DataStatus.UNKNOWN)

    def test_model_used_is_a_separate_field_from_requested_model(self) -> None:
        """Le modèle réellement utilisé n'est jamais supposé identique au
        served_model demandé — un champ distinct, jamais fusionné."""
        fields = GenerationResult.__dataclass_fields__
        self.assertIn("model_used", fields)


class TestModelCapabilityReport(unittest.TestCase):
    def test_is_available_true_only_for_available_status(self) -> None:
        for status in CapabilityStatus:
            with self.subTest(status=status):
                report = ModelCapabilityReport(status=status, backend="ollama", detail="d")
                self.assertEqual(report.is_available, status is CapabilityStatus.AVAILABLE)


if __name__ == "__main__":
    unittest.main()
