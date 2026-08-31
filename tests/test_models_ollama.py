"""Tests de OllamaProvider — détection RÉELLE (vrai HTTP, jamais un port
ouvert nu ni une réponse devinée), comportement honnête sans backend,
timeout et annulation réels contre un vrai serveur HTTP local (PAS Ollama —
un serveur de test qui imite exactement les réponses réelles d'Ollama, pour
prouver que ce code parle vraiment HTTP/JSON). Le seul test contre un VRAI
Ollama est explicitement sauté si aucun Ollama n'est réellement joignable —
jamais un faux PASS d'intégration.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
import unittest

from apex.config.schema import ModelEndpoint
from apex.errors import ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest
from apex.models.ollama import OllamaProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import DataStatus

_OLLAMA_MODEL = "qwen3-coder:30b"


def an_endpoint(url: str, **overrides) -> ModelEndpoint:
    defaults = dict(pool_id="local", runtime="ollama", url=url, served_model=_OLLAMA_MODEL, max_context=8192)
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def _real_ollama_url() -> str | None:
    """Constat réel — jamais supposé — qu'un vrai démon Ollama écoute en
    local. Utilisé UNIQUEMENT pour décider si le test d'intégration réel
    doit être sauté."""
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return "http://127.0.0.1:11434"
    except OSError:
        return None


class _FakeOllamaHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence pendant les tests
        pass

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            body = json.dumps({"models": self.server.tags}).encode("utf-8")  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        request = json.loads(raw.decode("utf-8")) if raw else {}
        if self.path != "/api/generate":
            self.send_response(404)
            self.end_headers()
            return
        if self.server.hang_s:  # type: ignore[attr-defined]
            time.sleep(self.server.hang_s)  # type: ignore[attr-defined]
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk in self.server.stream_chunks:  # type: ignore[attr-defined]
                self.wfile.write((json.dumps(chunk) + "\n").encode("utf-8"))
                self.wfile.flush()
                if self.server.stream_delay_s:  # type: ignore[attr-defined]
                    time.sleep(self.server.stream_delay_s)  # type: ignore[attr-defined]
        else:
            body = json.dumps(self.server.generate_response).encode("utf-8")  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)


class _FakeOllamaServer(http.server.HTTPServer):
    tags: list[dict] = []
    generate_response: dict = {}
    stream_chunks: list[dict] = []
    hang_s: float = 0.0
    stream_delay_s: float = 0.0


class _FakeOllamaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _FakeOllamaServer(("127.0.0.1", 0), _FakeOllamaHandler)
        self.server.tags = [{"name": _OLLAMA_MODEL}]
        self.server.generate_response = {
            "model": _OLLAMA_MODEL, "response": "réponse réelle du faux serveur", "done": True,
            "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 7,
            "total_duration": 123456, "eval_duration": 100,
        }
        self.server.stream_chunks = [
            {"model": _OLLAMA_MODEL, "response": "Bon", "done": False},
            {"model": _OLLAMA_MODEL, "response": "jour", "done": False},
            {"model": _OLLAMA_MODEL, "response": "", "done": True, "done_reason": "stop"},
        ]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TestDetectionAgainstThisRealMachine(unittest.TestCase):
    def test_default_ollama_port_is_unreachable_on_this_dev_machine(self) -> None:
        """Fait vérifié empiriquement : aucun démon Ollama n'écoute sur
        cette machine de développement (à la date de cette étape)."""
        if _real_ollama_url() is not None:
            self.skipTest("un vrai Ollama écoute sur cette machine — pas le scénario testé ici")
        provider = OllamaProvider(an_endpoint("http://127.0.0.1:11434"))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertFalse(report.facts["reachable"])

    def test_unreachable_is_never_declared_unsupported(self) -> None:
        provider = OllamaProvider(an_endpoint("http://127.0.0.1:18765"))  # port improbable
        report = provider.detect()
        self.assertNotEqual(report.status, CapabilityStatus.UNSUPPORTED)
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)


class TestDetectionAgainstARealLocalHttpServer(_FakeOllamaTestCase):
    """Preuve que detect() sonde RÉELLEMENT via HTTP (pas un port nu, pas un
    résultat câblé) : un vrai serveur HTTP local, imitant la forme réelle des
    réponses Ollama, est interrogé pour de vrai."""

    def test_reachable_with_model_present_is_available(self) -> None:
        provider = OllamaProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.facts["available_models"], [_OLLAMA_MODEL])

    def test_reachable_but_model_absent_is_unavailable_on_dev_not_unsupported(self) -> None:
        self.server.tags = [{"name": "some-other-model:7b"}]
        provider = OllamaProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)
        self.assertTrue(report.facts["reachable"])
        self.assertFalse(report.facts["model_present"])

    def test_reachable_and_unreachable_facts_are_never_confused(self) -> None:
        """false != null : reachable=True/False toujours un booléen constaté,
        jamais absent du dict."""
        provider = OllamaProvider(an_endpoint(self.url))
        report = provider.detect()
        self.assertIn("reachable", report.facts)
        self.assertIsInstance(report.facts["reachable"], bool)


class TestGenerateAgainstARealLocalHttpServer(_FakeOllamaTestCase):
    def test_successful_generation_maps_real_fields(self) -> None:
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="explique X", timeout_s=5.0)
        result = provider.generate(request)
        self.assertEqual(result.text, "réponse réelle du faux serveur")
        self.assertEqual(result.model_used, _OLLAMA_MODEL)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.tokens_prompt.status, DataStatus.REAL)
        self.assertEqual(result.tokens_prompt.value, 5)
        self.assertEqual(result.tokens_completion.value, 7)

    def test_missing_token_counts_are_honestly_unknown_not_zero(self) -> None:
        self.server.generate_response = {"model": _OLLAMA_MODEL, "response": "x", "done": True}
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        result = provider.generate(request)
        self.assertEqual(result.tokens_prompt.status, DataStatus.UNKNOWN)
        self.assertIsNone(result.tokens_prompt.value)

    def test_generate_refuses_when_model_absent_never_fabricates_a_result(self) -> None:
        self.server.tags = [{"name": "some-other-model:7b"}]
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        with self.assertRaises(ModelProviderError):
            provider.generate(request)

    def test_timeout_raises_timeout_exceeded_not_a_silent_hang(self) -> None:
        self.server.hang_s = 2.0
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=0.3)
        with self.assertRaises(TimeoutExceeded):
            provider.generate(request)

    def test_error_message_never_contains_the_prompt(self) -> None:
        """§72 : ni prompt ni réponse ne fuient dans un message d'erreur."""
        self.server.tags = []
        provider = OllamaProvider(an_endpoint(self.url))
        secret_prompt = "CECI_EST_UN_PROMPT_SENSIBLE_ne_doit_jamais_fuir"
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt=secret_prompt, timeout_s=5.0)
        with self.assertRaises(ModelProviderError) as ctx:
            provider.generate(request)
        self.assertNotIn(secret_prompt, str(ctx.exception))


class TestGenerateOnAnUnavailableBackend(unittest.TestCase):
    def test_generate_never_attempts_a_call_on_a_known_unreachable_endpoint(self) -> None:
        provider = OllamaProvider(an_endpoint("http://127.0.0.1:18765"))
        request = GenerationRequest(endpoint=an_endpoint("http://127.0.0.1:18765"), prompt="x", timeout_s=2.0)
        with self.assertRaises(ModelProviderError):
            provider.generate(request)


class TestGenerateStream(_FakeOllamaTestCase):
    def test_stream_yields_real_fragments_in_order(self) -> None:
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0)
        fragments = list(provider.generate_stream(request))
        self.assertEqual(fragments, ["Bon", "jour"])

    def test_stream_stops_when_cancel_event_is_already_set(self) -> None:
        event = threading.Event()
        event.set()
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0, cancel_event=event)
        fragments = list(provider.generate_stream(request))
        self.assertEqual(fragments, [])

    def test_stream_stops_early_when_cancelled_mid_flight(self) -> None:
        self.server.stream_delay_s = 0.2  # laisse le temps d'annuler entre deux fragments
        event = threading.Event()
        provider = OllamaProvider(an_endpoint(self.url))
        request = GenerationRequest(endpoint=an_endpoint(self.url), prompt="x", timeout_s=5.0, cancel_event=event)

        collected: list[str] = []
        gen = provider.generate_stream(request)
        collected.append(next(gen))
        event.set()
        collected.extend(gen)  # doit s'arrêter, pas continuer jusqu'au bout
        self.assertLess(len(collected), 2)


@unittest.skipUnless(_real_ollama_url() is not None, "aucun démon Ollama réel joignable sur cette machine")
class TestRealOllamaIntegration(unittest.TestCase):
    """Le SEUL test contre un vrai Ollama. Sauté explicitement, jamais
    remplacé par une simulation faisant croire à un PASS d'intégration."""

    def test_detect_reports_a_real_endpoint(self) -> None:
        url = _real_ollama_url()
        assert url is not None
        # Modèle probablement absent d'un Ollama fraîchement installé — le
        # test ne suppose que la JOIGNABILITÉ, jamais un modèle précis.
        provider = OllamaProvider(an_endpoint(url, served_model="__apex_probe_unlikely_model__"))
        report = provider.detect()
        self.assertTrue(report.facts["reachable"])


if __name__ == "__main__":
    unittest.main()
