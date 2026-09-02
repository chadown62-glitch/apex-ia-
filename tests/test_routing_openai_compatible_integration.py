"""Intégration réelle : ModelRouter -> generate_with_fallback -> dispatch
RÉEL (`get_default_provider`, aucun `provider_factory` injecté) ->
`OpenAICompatibleProvider` -> vrai HTTP contre un serveur de test qui imite
la forme exacte des réponses OpenAI-compatible.

Preuve recherchée : le raccordement du routeur à un provider externe
fonctionne de bout en bout SANS aucun code de câblage supplémentaire —
`_default_provider_factory` (`apex.routing.generation`) délègue déjà à
`get_default_provider`, qui dispatche déjà par `runtime`. Ce test n'ajoute
rien, il PROUVE que ce qui existe déjà se compose correctement.
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest

from apex.config.schema import ExternalPolicy, ModelEndpoint, ModelRef
from apex.models.registry import ModelCandidate
from apex.routing.generation import generate_with_fallback
from apex.routing.model import RoutingRequest
from apex.routing.router import select_model

_MODEL = "gpt-4o-mini-fixture"


class _FakeOpenAIHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/models":
            body = json.dumps({"data": [{"id": _MODEL}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({
            "id": "chatcmpl-fixture", "model": _MODEL,
            "choices": [{"message": {"role": "assistant", "content": "réponse réelle bout en bout"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class TestRouterToOpenAICompatibleEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_full_chain_selects_and_generates_via_real_dispatch(self) -> None:
        ref = ModelRef(family="gpt-4o-mini-fixture", version="1", quantization="external", capability_class="generalist")
        endpoint = ModelEndpoint(
            pool_id="cloud", runtime="openai_compatible", url=self.url,
            served_model=_MODEL, max_context=128000,  # secret_key=None : aucune auth exigée par le faux serveur
        )
        candidate = ModelCandidate(role="cloud_role", ref=ref, resident=False, endpoint=endpoint)
        policy = ExternalPolicy(
            enabled=True, allowed_contexts=frozenset({"research"}), daily_budget_eur=1.0,
            monthly_budget_eur=10.0, circuit_breaker_failures=3, circuit_breaker_cooldown_s=60,
        )

        decision = select_model([candidate], RoutingRequest(allow_external=True), external_policy=policy)
        self.assertEqual(decision.selected, candidate)

        ranked = [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0]
        # AUCUN provider_factory injecté — dispatch réel via get_default_provider.
        result = generate_with_fallback(ranked, prompt="explique X", timeout_s=5.0)

        self.assertIsNotNone(result.result)
        self.assertEqual(result.result.text, "réponse réelle bout en bout")
        self.assertEqual(result.succeeded_with, candidate)


if __name__ == "__main__":
    unittest.main()
