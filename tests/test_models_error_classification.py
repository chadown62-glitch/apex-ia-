"""Tests de la classification RÉELLE des erreurs `ModelProvider` (passe
« Routing & Execution Observability Hardening », N+18) — contre un VRAI
serveur HTTP local (même patron que `tests/test_models_openai_compatible.
py`), jamais un mock au niveau `urllib`. Vérifie que `ModelProviderError.
category`/`.http_status` reflètent exactement le signal structurel observé
— 401/403 -> AUTH_ERROR, 429 -> RATE_LIMITED, 400 avec `error.code ==
"context_length_exceeded"` -> CONTEXT_TOO_LONG (jamais depuis le seul code
400, qui a d'autres causes), 500 -> PROVIDER_ERROR (jamais UNKNOWN : le
code HTTP EST un signal réel), connexion impossible -> OFFLINE, timeout ->
`TimeoutExceeded` (catégorie unifiée `TIMEOUT` au niveau de la tentative,
`apex.routing.generation`).
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest

from apex.config.schema import ModelEndpoint
from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest
from apex.models.ollama import OllamaProvider
from apex.models.openai_compatible import OpenAICompatibleProvider

_MODEL = "fixture-model"


class _StatusHandler(http.server.BaseHTTPRequestHandler):
    """Sert TOUJOURS `server.next_status`/`server.next_body` sur n'importe
    quelle requête — un double de test volontairement générique, jamais
    spécifique à un provider (les deux providers testés ici tapent des
    chemins différents, tous deux gérés par ce même handler)."""

    def log_message(self, *args: object) -> None:
        pass

    def _reply(self) -> None:
        status = self.server.next_status  # type: ignore[attr-defined]
        body = self.server.next_body  # type: ignore[attr-defined]
        if self.path in ("/api/tags", "/models"):
            # Sondes de détection — toujours saines, seule la génération
            # doit échouer dans ces tests.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path == "/api/tags":
                self.wfile.write(json.dumps({"models": [{"name": _MODEL}]}).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"data": [{"id": _MODEL}]}).encode("utf-8"))
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._reply()

    def do_POST(self) -> None:
        self._reply()


class _StatusServer(http.server.HTTPServer):
    next_status: int = 200
    next_body: dict = {}


class _ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _StatusServer(("127.0.0.1", 0), _StatusHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _set_response(self, status: int, body: dict) -> None:
        self.server.next_status = status
        self.server.next_body = body

    def _request(self) -> GenerationRequest:
        return GenerationRequest(endpoint=self.endpoint, prompt="x", timeout_s=5.0)


class TestOpenAICompatibleErrorClassification(_ServerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.endpoint = ModelEndpoint(
            pool_id="cloud", runtime="openai_compatible", url=self.url, served_model=_MODEL, max_context=8192,
        )
        self.provider = OpenAICompatibleProvider(self.endpoint)

    def test_429_is_rate_limited(self) -> None:
        self._set_response(429, {"error": {"message": "too many requests"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.RATE_LIMITED)
        self.assertEqual(ctx.exception.http_status, 429)

    def test_401_is_auth_error(self) -> None:
        self._set_response(401, {"error": {"message": "invalid api key"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.AUTH_ERROR)

    def test_403_is_auth_error(self) -> None:
        self._set_response(403, {"error": {"message": "forbidden"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.AUTH_ERROR)

    def test_400_with_structured_context_length_code_is_context_too_long(self) -> None:
        self._set_response(400, {"error": {"message": "trop long", "code": "context_length_exceeded"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.CONTEXT_TOO_LONG)

    def test_400_without_the_structured_code_is_never_guessed_as_context_too_long(self) -> None:
        """Un 400 ordinaire (validation, champ manquant...) reste
        `PROVIDER_ERROR` — jamais promu à `CONTEXT_TOO_LONG` sans le champ
        structuré exact."""
        self._set_response(400, {"error": {"message": "champ manquant", "code": "invalid_request"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.PROVIDER_ERROR)

    def test_400_with_unparseable_body_falls_back_to_provider_error(self) -> None:
        self.server.next_status = 400
        self.server.next_body = {}  # corps vide, aucun champ error.code
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.PROVIDER_ERROR)

    def test_500_is_provider_error_never_unknown(self) -> None:
        """Le code HTTP EST un signal réel, même sans catégorie plus
        précise — jamais `UNKNOWN` (réservé à l'absence totale de signal)."""
        self._set_response(500, {"error": {"message": "internal error"}})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.PROVIDER_ERROR)
        self.assertEqual(ctx.exception.http_status, 500)

    def test_connection_refused_is_offline(self) -> None:
        unreachable = ModelEndpoint(
            pool_id="cloud", runtime="openai_compatible", url="http://127.0.0.1:1",
            served_model=_MODEL, max_context=8192,
        )
        provider = OpenAICompatibleProvider(unreachable, connect_timeout_s=0.5)
        with self.assertRaises(ModelProviderError) as ctx:
            provider.generate(GenerationRequest(endpoint=unreachable, prompt="x", timeout_s=1.0))
        self.assertEqual(ctx.exception.category, ModelErrorCategory.OFFLINE)


class TestOllamaErrorClassification(_ServerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.endpoint = ModelEndpoint(pool_id="local", runtime="ollama", url=self.url, served_model=_MODEL, max_context=8192)
        self.provider = OllamaProvider(self.endpoint)

    def test_429_is_rate_limited(self) -> None:
        self._set_response(429, {"error": "rate limited"})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.RATE_LIMITED)

    def test_500_is_provider_error(self) -> None:
        self._set_response(500, {"error": "internal error"})
        with self.assertRaises(ModelProviderError) as ctx:
            self.provider.generate(self._request())
        self.assertEqual(ctx.exception.category, ModelErrorCategory.PROVIDER_ERROR)

    def test_connection_refused_is_offline(self) -> None:
        unreachable = ModelEndpoint(pool_id="local", runtime="ollama", url="http://127.0.0.1:1", served_model=_MODEL, max_context=8192)
        provider = OllamaProvider(unreachable, connect_timeout_s=0.5)
        with self.assertRaises(ModelProviderError) as ctx:
            provider.generate(GenerationRequest(endpoint=unreachable, prompt="x", timeout_s=1.0))
        self.assertEqual(ctx.exception.category, ModelErrorCategory.OFFLINE)


class TestTimeoutIsNeverAModelProviderError(_ServerTestCase):
    """Le timeout reste une exception DISTINCTE (`apex.errors.
    TimeoutExceeded`) — jamais une catégorie de `ModelProviderError` — mais
    `apex.routing.generation._attempt` l'unifie sous `ModelErrorCategory.
    TIMEOUT` pour la classification d'une tentative (voir
    `tests/test_routing_generation_categories.py`)."""

    def test_slow_response_raises_timeout_exceeded_not_model_provider_error(self) -> None:
        import time

        class _SlowHandler(_StatusHandler):
            def _reply(self) -> None:  # noqa: D401 — délibérément lent
                time.sleep(0.3)
                super()._reply()

        self.server.RequestHandlerClass = _SlowHandler  # type: ignore[assignment]
        endpoint = ModelEndpoint(pool_id="cloud", runtime="openai_compatible", url=self.url, served_model=_MODEL, max_context=8192)
        provider = OpenAICompatibleProvider(endpoint)
        with self.assertRaises(TimeoutExceeded):
            provider.generate(GenerationRequest(endpoint=endpoint, prompt="x", timeout_s=0.05))


if __name__ == "__main__":
    unittest.main()
