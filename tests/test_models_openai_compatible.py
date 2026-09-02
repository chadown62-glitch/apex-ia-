"""Tests d'`OpenAICompatibleProvider` — détection RÉELLE (vrai HTTP, jamais
un port ouvert nu ni une réponse devinée), authentification via
`SecretBroker` (jamais une clé en dur — voir `TestNoRealSecretMaterial`),
comportement honnête sans backend, jamais de fuite du token dans une
erreur (§72). Le seul test contre un VRAI endpoint externe est
explicitement sauté si aucune clé/URL réelle n'est fournie — jamais un
faux PASS d'intégration (même discipline que `tests/test_models_ollama.py`).
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import unittest

from apex.config.schema import ModelEndpoint
from apex.errors import ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest
from apex.models.openai_compatible import OpenAICompatibleProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import DataStatus
from apex.secrets.provider import EnvSecretBroker

_MODEL = "gpt-4o-mini-fixture"


def an_endpoint(url: str, **overrides) -> ModelEndpoint:
    defaults = dict(pool_id="cloud", runtime="openai_compatible", url=url, served_model=_MODEL, max_context=128000)
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class _FakeOpenAIHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _check_auth(self) -> bool:
        expected = self.server.expected_auth_header  # type: ignore[attr-defined]
        if expected is None:
            return True
        return self.headers.get("Authorization") == expected

    def do_GET(self) -> None:
        if self.path == "/models":
            if not self._check_auth():
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"data": self.server.models}).encode("utf-8")  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        if not self._check_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "invalid api key"}}).encode("utf-8"))
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        json.loads(raw.decode("utf-8")) if raw else {}
        body = json.dumps(self.server.completion_response).encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class _FakeOpenAIServer(http.server.HTTPServer):
    models: list[dict] = []
    completion_response: dict = {}
    expected_auth_header: str | None = None


class _FakeServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _FakeOpenAIServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        self.server.models = [{"id": _MODEL}]
        self.server.completion_response = {
            "id": "chatcmpl-fixture", "model": _MODEL,
            "choices": [{"message": {"role": "assistant", "content": "réponse réelle du faux serveur"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        self.server.expected_auth_header = None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TestDetectionWithoutAuthRequired(_FakeServerTestCase):
    def test_reachable_with_model_present_is_available(self) -> None:
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.facts["available_models"], [_MODEL])

    def test_reachable_but_model_absent_is_unavailable_not_unsupported(self) -> None:
        self.server.models = [{"id": "some-other-model"}]
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertFalse(report.facts["model_present"])


class TestDetectionOnAnUnreachableEndpoint(unittest.TestCase):
    def test_unreachable_is_never_declared_unsupported(self) -> None:
        provider = OpenAICompatibleProvider(an_endpoint("http://127.0.0.1:18765"))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertNotEqual(report.status, CapabilityStatus.UNSUPPORTED)


class TestSecretBrokerIntegration(_FakeServerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server.expected_auth_header = "Bearer test-fixture-token"

    def test_missing_secret_is_unavailable_never_a_bare_call(self) -> None:
        """Aucune tentative HTTP faite sans clé résolue — même sur un
        endpoint réellement joignable."""
        broker = EnvSecretBroker(env={})
        provider = OpenAICompatibleProvider(
            an_endpoint(self.url, secret_key="openai_api_key"), secret_broker=broker,
        )
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertFalse(report.facts["secret_resolved"])
        self.assertFalse(report.facts["reachable"])

    def test_resolved_secret_authenticates_successfully(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "test-fixture-token"})
        provider = OpenAICompatibleProvider(
            an_endpoint(self.url, secret_key="openai_api_key"), secret_broker=broker,
        )
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)

    def test_wrong_secret_value_is_rejected_by_the_server_not_faked_as_success(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "wrong-token"})
        provider = OpenAICompatibleProvider(
            an_endpoint(self.url, secret_key="openai_api_key"), secret_broker=broker,
        )
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_no_secret_key_means_no_auth_required(self) -> None:
        """`secret_key=None` (défaut) : aucune authentification exigée —
        distinct d'une clé manquante."""
        self.server.expected_auth_header = None
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)


class TestGenerate(_FakeServerTestCase):
    def test_successful_generation_maps_real_fields(self) -> None:
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="explique X", timeout_s=5.0)
        result = provider.generate(request)
        self.assertEqual(result.text, "réponse réelle du faux serveur")
        self.assertEqual(result.model_used, _MODEL)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.tokens_prompt.status, DataStatus.REAL)
        self.assertEqual(result.tokens_prompt.value, 5)
        self.assertEqual(result.tokens_completion.value, 7)

    def test_missing_usage_is_honestly_unknown_not_zero(self) -> None:
        self.server.completion_response = {
            "id": "x", "model": _MODEL,
            "choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}],
        }
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        result = provider.generate(request)
        self.assertEqual(result.tokens_prompt.status, DataStatus.UNKNOWN)
        self.assertIsNone(result.tokens_prompt.value)

    def test_generate_refuses_when_model_absent_never_fabricates_a_result(self) -> None:
        self.server.models = [{"id": "some-other-model"}]
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        with self.assertRaises(ModelProviderError):
            provider.generate(request)

    def test_error_message_never_contains_the_prompt(self) -> None:
        self.server.models = []
        provider = OpenAICompatibleProvider(an_endpoint(self.url))
        secret_prompt = "CECI_EST_UN_PROMPT_SENSIBLE_ne_doit_jamais_fuir"
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt=secret_prompt, timeout_s=5.0)
        with self.assertRaises(ModelProviderError) as ctx:
            provider.generate(request)
        self.assertNotIn(secret_prompt, str(ctx.exception))

    def test_error_message_never_contains_the_bearer_token(self) -> None:
        self.server.expected_auth_header = "Bearer test-fixture-token-should-never-leak"
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "wrong-token"})
        provider = OpenAICompatibleProvider(
            an_endpoint(self.url, secret_key="openai_api_key"), secret_broker=broker,
        )
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        with self.assertRaises(ModelProviderError) as ctx:
            provider.generate(request)
        self.assertNotIn("wrong-token", str(ctx.exception))
        self.assertNotIn("test-fixture-token-should-never-leak", str(ctx.exception))


class TestGenerateOnAnUnavailableBackend(unittest.TestCase):
    def test_generate_never_attempts_a_call_on_a_known_unreachable_endpoint(self) -> None:
        provider = OpenAICompatibleProvider(an_endpoint("http://127.0.0.1:18765"))
        request = GenerationRequest(endpoint=an_endpoint("http://127.0.0.1:18765"), prompt="x", timeout_s=2.0)
        with self.assertRaises(ModelProviderError):
            provider.generate(request)


def _real_endpoint_url() -> str | None:
    """Constat réel — jamais supposé. Aucune valeur par défaut devinée :
    un vrai endpoint OpenAI-compatible n'est jamais local par convention
    (contrairement à Ollama), donc rien n'est sondé sans configuration
    explicite."""
    return os.environ.get("APEX_OPENAI_COMPATIBLE_URL")


@unittest.skipUnless(_real_endpoint_url() is not None, "aucun endpoint OpenAI-compatible réel configuré (APEX_OPENAI_COMPATIBLE_URL)")
class TestRealOpenAICompatibleIntegration(unittest.TestCase):
    """Le SEUL test de ce fichier contre un VRAI service externe — sauté
    explicitement tant qu'aucune configuration réelle n'est fournie,
    jamais remplacé par une simulation faisant croire à un PASS."""

    def test_detect_reports_a_real_endpoint(self) -> None:
        url = _real_endpoint_url()
        assert url is not None
        model = os.environ.get("APEX_OPENAI_COMPATIBLE_MODEL", "gpt-4o-mini")
        secret_key = os.environ.get("APEX_OPENAI_COMPATIBLE_SECRET_KEY", "openai_api_key")
        provider = OpenAICompatibleProvider(an_endpoint(url, served_model=model, secret_key=secret_key))
        report = provider.detect()
        self.assertTrue(report.facts.get("reachable"))


if __name__ == "__main__":
    unittest.main()
