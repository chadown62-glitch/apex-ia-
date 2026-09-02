"""Tests d'`apex.models.catalog` — vues DÉRIVÉES (jamais une seconde source
de vérité) au-dessus d'`ApexConfig`/`apex.models.registry`/`apex.routing`
déjà réels. Aucune DB. Le seul test qui touche le réseau (`discover_live`)
utilise un vrai serveur HTTP local, jamais un mock qui ne prouve rien.
"""

from __future__ import annotations

import copy
import http.server
import json
import threading
import unittest

from apex.config.loader import parse_config
from apex.config.schema import ExternalPolicy, ModelEndpoint, ModelRef
from apex.errors import ModelProviderError
from apex.obs.capability import CapabilityStatus
from apex.models.catalog import (
    Availability,
    CatalogModel,
    DiscoverySource,
    ProviderInfo,
    build_model_catalog,
    build_provider_catalog,
    discover_live,
    filter_by_availability,
    filter_by_capabilities,
    filter_by_min_context,
    filter_by_runtime,
    filter_local,
    usable_candidates,
)
from apex.models.model import ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate, resolve_candidates
from apex.routing.generation import generate_with_fallback
from apex.routing.model import Capability, RoutingRequest, derive_capabilities
from apex.routing.router import select_model

BASE: dict = {
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
            "coder_main": {
                "candidates": [
                    {"family": "qwen2.5-coder", "version": "7b-instruct",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                ],
            },
            "judge": {
                "candidates": [
                    {"family": "gemma4", "version": "12b",
                     "quantization": "q4_k_m", "capability_class": "judge"},
                ],
            },
            "unavailable_role": {
                "candidates": [
                    {"family": "phantom-model", "version": "1b",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
            "disabled_role": {
                "candidates": [
                    {"family": "sleeping-model", "version": "1b",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
        },
        "endpoints": [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen2.5-coder:7b-instruct", "max_context": 32768, "vram_gb": 5.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "gemma4:12b-q4_K_M", "max_context": 16384, "vram_gb": 8.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "sleeping-model:1b-q4_K_M", "max_context": 8192, "vram_gb": 1.0,
             "enabled": False},
        ],
    },
    "external": {"enabled": False, "allowed_contexts": ["interactive", "research"]},
    "budget": {"nightly_gpu_minutes": 600, "max_experiment_gpu_minutes": 240},
    "sandbox": {"provider": "docker", "network": "none"},
    "languages": {"python": {"wave": 1, "image": "python:3.12-slim", "oracle_strength": "strong"}},
    "services": {"postgres": {"host": "127.0.0.1", "port": 5432}},
}


def config(**overrides) -> dict:
    document = copy.deepcopy(BASE)
    for path, value in overrides.items():
        node = document
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return document


class TestBuildModelCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())
        self.catalog = build_model_catalog(self.cfg)

    def test_one_entry_per_resolved_candidate_no_duplication(self) -> None:
        """Le catalogue ne réinvente rien : même longueur et mêmes
        candidats que `resolve_candidates`, jamais une seconde résolution
        divergente."""
        candidates = resolve_candidates(self.cfg)
        self.assertEqual(len(self.catalog), len(candidates))
        self.assertEqual({m.candidate for m in self.catalog}, set(candidates))

    def test_capabilities_match_router_derivation_exactly(self) -> None:
        """Aucun vocabulaire concurrent — les capacités du catalogue SONT
        `apex.routing.model.derive_capabilities`, jamais recalculées
        différemment."""
        for model in self.catalog:
            self.assertEqual(model.capabilities, derive_capabilities(model.candidate))

    def test_disabled_endpoint_yields_disabled_availability(self) -> None:
        disabled = [m for m in self.catalog if m.candidate.role == "disabled_role"]
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0].availability, Availability.DISABLED)

    def test_declared_but_never_probed_is_unknown_never_promoted(self) -> None:
        """Un candidat avec endpoint actif, jamais sondé, reste `UNKNOWN` —
        jamais promu à `AVAILABLE` sans preuve."""
        coder = [m for m in self.catalog if m.candidate.role == "coder_main"][0]
        self.assertEqual(coder.availability, Availability.UNKNOWN)

    def test_candidate_without_endpoint_is_unknown(self) -> None:
        unavailable = [m for m in self.catalog if m.candidate.role == "unavailable_role"][0]
        self.assertIsNone(unavailable.candidate.endpoint)
        self.assertEqual(unavailable.availability, Availability.UNKNOWN)

    def test_discovery_source_is_configured_before_any_live_probe(self) -> None:
        for model in self.catalog:
            self.assertEqual(model.discovery, DiscoverySource.CONFIGURED)

    def test_performance_history_is_empty_never_invented(self) -> None:
        for model in self.catalog:
            self.assertTrue(model.history.is_empty)
            self.assertEqual(model.history.usage_count, 0)

    def test_model_id_and_display_name(self) -> None:
        coder = [m for m in self.catalog if m.candidate.role == "coder_main"][0]
        self.assertEqual(coder.model_id, "qwen2.5-coder:7b-instruct:q4_k_m")
        self.assertEqual(coder.display_name, "qwen2.5-coder:7b-instruct")


class TestBuildProviderCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())
        self.providers = build_provider_catalog(self.cfg)

    def test_endpoints_on_the_same_runtime_url_secret_group_into_one_provider(self) -> None:
        """3 endpoints déclarés, même (runtime, url, secret_key=None) ->
        UN SEUL provider, jamais une liste dupliquée par endpoint."""
        self.assertEqual(len(self.providers), 1)
        provider = self.providers[0]
        self.assertEqual(provider.runtime, "ollama")
        self.assertEqual(len(provider.model_ids), 3)

    def test_provider_enabled_true_if_any_endpoint_enabled(self) -> None:
        """Le groupe contient un endpoint désactivé (sleeping-model) et deux
        actifs -> le provider lui-même reste utilisable."""
        self.assertTrue(self.providers[0].enabled)

    def test_provider_local_matches_get_default_provider_dispatch_signal(self) -> None:
        """`ProviderInfo.local` réutilise le même signal que
        `apex.models.provider.get_default_provider` (`runtime == "ollama"`),
        jamais une heuristique concurrente."""
        self.assertTrue(self.providers[0].local)

    def test_provider_id_is_deterministic(self) -> None:
        again = build_provider_catalog(self.cfg)
        self.assertEqual(self.providers[0].id, again[0].id)
        self.assertEqual(self.providers[0].id, "ollama:http://127.0.0.1:11434")

    def test_external_endpoint_with_secret_key_is_a_distinct_provider(self) -> None:
        document = config()
        document["models"]["roles"]["cloud_role"] = {
            "candidates": [
                {"family": "gpt-5", "version": "1", "quantization": "external",
                 "capability_class": "generalist"},
            ],
        }
        document["compute"]["pools"]["cloud"] = {
            "location": "cloud", "trust_level": "untrusted",
            "capabilities": ["inference"], "cost_per_hour": 1.6,
        }
        document["models"]["endpoints"].append({
            "pool_id": "cloud", "runtime": "openai_compatible", "url": "https://api.example.com/v1",
            "served_model": "gpt-5", "max_context": 128000, "secret_key": "openai_api_key",
        })
        providers = build_provider_catalog(parse_config(document))
        self.assertEqual(len(providers), 2)
        cloud = [p for p in providers if p.runtime == "openai_compatible"][0]
        self.assertFalse(cloud.local)
        # secret_key n'est JAMAIS la valeur — seulement l'identifiant logique
        # déclaré en config, une chaîne stable comparable telle quelle.
        self.assertEqual(cloud.secret_key, "openai_api_key")
        self.assertNotIn("sk-", cloud.secret_key)


class TestFilters(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())
        self.catalog = build_model_catalog(self.cfg)

    def test_filter_by_capabilities(self) -> None:
        coders = filter_by_capabilities(self.catalog, frozenset({Capability.CODING}))
        self.assertEqual(len(coders), 1)
        self.assertEqual(coders[0].candidate.role, "coder_main")

    def test_filter_by_capabilities_text_matches_everything(self) -> None:
        """`TEXT` est dérivée pour tout candidat réel — un filtre dessus
        n'exclut jamais rien ici."""
        texts = filter_by_capabilities(self.catalog, frozenset({Capability.TEXT}))
        self.assertEqual(len(texts), len(self.catalog))

    def test_filter_by_availability_disabled(self) -> None:
        disabled = filter_by_availability(self.catalog, allowed=frozenset({Availability.DISABLED}))
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0].candidate.role, "disabled_role")

    def test_filter_by_availability_unknown(self) -> None:
        unknown = filter_by_availability(self.catalog, allowed=frozenset({Availability.UNKNOWN}))
        self.assertEqual(len(unknown), 3)  # coder_main, judge, unavailable_role

    def test_filter_by_runtime(self) -> None:
        ollama = filter_by_runtime(self.catalog, "ollama")
        self.assertEqual(len(ollama), 3)  # tous sauf unavailable_role (pas d'endpoint)

    def test_filter_local_excludes_nothing_when_none_are_external(self) -> None:
        self.assertEqual(len(filter_local(self.catalog, local=True)), len(self.catalog))
        self.assertEqual(len(filter_local(self.catalog, local=False)), 0)

    def test_filter_by_min_context(self) -> None:
        fits = filter_by_min_context(self.catalog, 20000)
        self.assertEqual(len(fits), 1)
        self.assertEqual(fits[0].candidate.role, "coder_main")


class TestUsableCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())
        self.catalog = build_model_catalog(self.cfg)

    def test_disabled_is_excluded(self) -> None:
        usable = usable_candidates(self.catalog)
        roles = {c.role for c in usable}
        self.assertNotIn("disabled_role", roles)

    def test_unknown_is_included_never_pre_rejected(self) -> None:
        """`UNKNOWN` (jamais sondé) n'est pas la même chose que refusé —
        `select_model` garde le dernier mot sur l'éligibilité."""
        usable = usable_candidates(self.catalog)
        roles = {c.role for c in usable}
        self.assertIn("coder_main", roles)
        self.assertIn("judge", roles)

    def test_never_a_second_independent_registry(self) -> None:
        """Les objets extraits SONT les `ModelCandidate` d'origine (identité,
        pas une copie recomposée) — la preuve qu'aucune seconde source de
        vérité n'a été introduite."""
        usable = usable_candidates(self.catalog)
        original = {c.candidate for c in self.catalog if c.availability != Availability.DISABLED}
        self.assertEqual(set(usable), original)


class _FailingProvider(ModelProvider):
    """Sonde qui échoue toujours — vérifie qu'un secret déclaré (secret_key)
    ne fuit jamais dans le message d'erreur ni dans le rapport résultant."""

    def __init__(self, endpoint) -> None:
        self._endpoint = endpoint

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.UNAVAILABLE_ON_DEV,
            backend=self._endpoint.runtime,
            detail=f"connexion refusée pour {self._endpoint.url}",
        )

    def generate(self, request):  # pragma: no cover - jamais appelé ici
        raise ModelProviderError("non appelé")


class _SucceedingProvider(ModelProvider):
    def __init__(self, endpoint) -> None:
        self._endpoint = endpoint

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend=self._endpoint.runtime,
            detail=f"{self._endpoint.served_model} joignable",
        )

    def generate(self, request):  # pragma: no cover - jamais appelé ici
        raise ModelProviderError("non appelé")


class TestDiscoverLive(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())
        self.catalog = build_model_catalog(self.cfg)

    def test_disabled_is_never_probed(self) -> None:
        calls: list[str] = []

        def factory(model: CatalogModel):
            calls.append(model.candidate.role)
            return _SucceedingProvider(model.candidate.endpoint)

        resolved = discover_live(self.catalog, provider_factory=factory)
        self.assertNotIn("disabled_role", calls)
        disabled = [m for m in resolved if m.candidate.role == "disabled_role"][0]
        self.assertEqual(disabled.availability, Availability.DISABLED)

    def test_candidate_without_endpoint_is_never_probed(self) -> None:
        calls: list[str] = []

        def factory(model: CatalogModel):
            calls.append(model.candidate.role)
            return _SucceedingProvider(model.candidate.endpoint)

        resolved = discover_live(self.catalog, provider_factory=factory)
        self.assertNotIn("unavailable_role", calls)
        still_unknown = [m for m in resolved if m.candidate.role == "unavailable_role"][0]
        self.assertEqual(still_unknown.availability, Availability.UNKNOWN)

    def test_successful_probe_yields_available_and_discovered(self) -> None:
        resolved = discover_live(
            self.catalog, provider_factory=lambda m: _SucceedingProvider(m.candidate.endpoint)
        )
        coder = [m for m in resolved if m.candidate.role == "coder_main"][0]
        self.assertEqual(coder.availability, Availability.AVAILABLE)
        self.assertEqual(coder.discovery, DiscoverySource.DISCOVERED)

    def test_failed_probe_yields_unavailable_never_available(self) -> None:
        resolved = discover_live(
            self.catalog, provider_factory=lambda m: _FailingProvider(m.candidate.endpoint)
        )
        coder = [m for m in resolved if m.candidate.role == "coder_main"][0]
        self.assertEqual(coder.availability, Availability.UNAVAILABLE)

    def test_provider_error_during_probe_is_caught_as_unavailable(self) -> None:
        class _RaisingProvider(ModelProvider):
            def __init__(self, endpoint) -> None:
                pass

            def detect(self) -> ModelCapabilityReport:
                raise ModelProviderError(
                    "openai_compatible", endpoint_url="https://api.example.com/v1",
                    detail="connexion refusée",
                )

            def generate(self, request):  # pragma: no cover
                raise ModelProviderError("non appelé")

        resolved = discover_live(self.catalog, provider_factory=lambda m: _RaisingProvider(m.candidate.endpoint))
        coder = [m for m in resolved if m.candidate.role == "coder_main"][0]
        self.assertEqual(coder.availability, Availability.UNAVAILABLE)

    def test_no_secret_value_appears_anywhere_in_catalog_or_errors(self) -> None:
        document = config()
        document["models"]["roles"]["cloud_role"] = {
            "candidates": [
                {"family": "gpt-5", "version": "1", "quantization": "external",
                 "capability_class": "generalist"},
            ],
        }
        document["compute"]["pools"]["cloud"] = {
            "location": "cloud", "trust_level": "untrusted",
            "capabilities": ["inference"], "cost_per_hour": 1.6,
        }
        document["models"]["endpoints"].append({
            "pool_id": "cloud", "runtime": "openai_compatible", "url": "https://api.example.com/v1",
            "served_model": "gpt-5", "max_context": 128000, "secret_key": "openai_api_key",
        })
        cfg = parse_config(document)
        catalog = build_model_catalog(cfg)
        resolved = discover_live(catalog, provider_factory=lambda m: _FailingProvider(m.candidate.endpoint))
        # `secret_key` reste l'identifiant LOGIQUE partout, jamais une vraie
        # valeur de secret (qui ne figure d'ailleurs nulle part en config).
        for model in resolved:
            self.assertNotIn("sk-", repr(model))
            self.assertNotIn("Bearer", repr(model))


class TestCatalogRouterIntegration(unittest.TestCase):
    """Le catalogue est un pont vers le Router, jamais un chemin parallèle :
    `usable_candidates(build_model_catalog(cfg))` doit produire exactement
    ce que `resolve_candidates(cfg)` filtré à la main produirait."""

    def setUp(self) -> None:
        self.cfg = parse_config(config())

    def test_select_model_over_catalog_matches_select_model_over_registry(self) -> None:
        from_catalog = usable_candidates(build_model_catalog(self.cfg))
        from_registry = tuple(
            c for c in resolve_candidates(self.cfg) if c.role != "disabled_role"
        )
        request = RoutingRequest(required_capabilities=frozenset({Capability.CODING}))
        decision_catalog = select_model(list(from_catalog), request)
        decision_registry = select_model(list(from_registry), request)
        self.assertEqual(decision_catalog.selected, decision_registry.selected)
        self.assertIsNotNone(decision_catalog.selected)
        self.assertEqual(decision_catalog.selected.role, "coder_main")

    def test_disabled_never_reaches_the_router(self) -> None:
        candidates = usable_candidates(build_model_catalog(self.cfg))
        request = RoutingRequest(required_capabilities=frozenset())
        decision = select_model(list(candidates), request)
        selected_roles = {sc.candidate.role for sc in decision.ranked_candidates}
        self.assertNotIn("disabled_role", selected_roles)


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
            "choices": [{"message": {"role": "assistant", "content": "catalogue -> routeur -> provider réel"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class TestCatalogToRealProviderEndToEnd(unittest.TestCase):
    """Preuve de bout en bout exigée : `RoutingRequest` -> Model Catalog ->
    Router -> `get_default_provider()` -> vrai `OpenAICompatibleProvider`
    contre un vrai serveur HTTP local (jamais un appel payant)."""

    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_full_chain_from_catalog_to_real_http(self) -> None:
        document = config()
        document["models"]["roles"]["cloud_role"] = {
            "candidates": [
                {"family": "gpt-4o-mini-fixture", "version": "1", "quantization": "external",
                 "capability_class": "generalist"},
            ],
        }
        document["compute"]["pools"]["cloud"] = {
            "location": "cloud", "trust_level": "untrusted",
            "capabilities": ["inference"], "cost_per_hour": 1.6,
        }
        document["models"]["endpoints"].append({
            "pool_id": "cloud", "runtime": "openai_compatible", "url": self.url,
            "served_model": _MODEL, "max_context": 128000,
        })  # pas de secret_key : le faux serveur n'exige aucune auth
        cfg = parse_config(document)

        catalog = build_model_catalog(cfg)
        cloud_model = [m for m in catalog if m.candidate.role == "cloud_role"][0]
        self.assertEqual(cloud_model.availability, Availability.UNKNOWN)  # jamais sondé encore

        candidates = usable_candidates(catalog)
        policy = ExternalPolicy(
            enabled=True, allowed_contexts=frozenset({"research"}), daily_budget_eur=1.0,
            monthly_budget_eur=10.0, circuit_breaker_failures=3, circuit_breaker_cooldown_s=60,
        )
        request = RoutingRequest(allow_external=True, required_capabilities=frozenset({Capability.GENERAL}))
        decision = select_model(list(candidates), request, external_policy=policy)
        self.assertIsNotNone(decision.selected)
        self.assertEqual(decision.selected.role, "cloud_role")

        ranked = [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0]
        result = generate_with_fallback(ranked, prompt="explique X", timeout_s=5.0)

        self.assertIsNotNone(result.result)
        self.assertEqual(result.result.text, "catalogue -> routeur -> provider réel")
        self.assertEqual(result.succeeded_with, decision.selected)


if __name__ == "__main__":
    unittest.main()
