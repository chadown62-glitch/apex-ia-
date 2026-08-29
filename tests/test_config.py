"""Tests de configuration.

Écrits en `unittest` : exécutables par `python -m unittest` (stdlib, aucune
dépendance) comme par `pytest`. Cela garantit qu'ils tournent même sur un
environnement où les dépendances ne sont pas installées — le même argument que
pour le doctor.

Les cas les plus importants ici ne testent pas le parsing mais les
**invariants des principes verrouillés** : indépendance du Judge, interdiction
d'un modèle externe dans la boucle autonome, contextes externes en liste
blanche. Ce sont des tests de politique, et ils doivent échouer bruyamment.
"""

from __future__ import annotations

import copy
import unittest

from apex.config.loader import parse_config, substitute_env
from apex.config.schema import PoolLocation, TrustLevel
from apex.errors import ConfigError

BASE: dict = {
    "version": 1,
    "phase": 1,
    "paths": {
        "data_root": "/home/apex/data",
        "workspaces": "/home/apex/data/workspaces",
        "artifacts": "/home/apex/data/artifacts",
        "logs": "/home/apex/data/logs",
    },
    "compute": {
        "pools": {
            "local-gpu": {
                "location": "local",
                "trust_level": "trusted",
                "capabilities": ["inference", "sandbox"],
                "gpus": [{"model": "RTX 4090", "vram_gb": 24}],
                "cpu_cores": 12,
                "ram_gb": 48,
            }
        }
    },
    "models": {
        "roles": {
            "coder_main": {
                "selection": "benchmark_driven",
                "candidates": [
                    {"family": "qwen3-coder", "version": "30b-a3b-instruct",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                    {"family": "devstral-2", "version": "22b",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                ],
            },
            "judge": {
                "candidates": [
                    {"family": "gemma4", "version": "12b",
                     "quantization": "q4_k_m", "capability_class": "judge"}
                ]
            },
        },
        "endpoints": [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:30b", "max_context": 65536, "vram_gb": 18.0}
        ],
    },
    "external": {
        "enabled": False,
        "allowed_contexts": ["interactive", "research"],
    },
    "budget": {"nightly_gpu_minutes": 600, "max_experiment_gpu_minutes": 240},
    "sandbox": {"provider": "docker", "network": "none"},
    "languages": {
        "python": {"wave": 1, "image": "python:3.12-slim", "oracle_strength": "strong"},
        "cpp": {"wave": 2, "image": "silkeh/clang:18", "oracle_strength": "medium",
                "separate_metrics": True},
    },
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


class TestParsing(unittest.TestCase):
    def test_valid_config_parses(self) -> None:
        cfg = parse_config(config())
        self.assertEqual(cfg.version, 1)
        self.assertIn("local-gpu", cfg.pools)
        self.assertEqual(cfg.pools["local-gpu"].total_vram_gb, 24.0)
        self.assertIs(cfg.pools["local-gpu"].location, PoolLocation.LOCAL)

    def test_unsupported_version_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(config(version=99))

    def test_missing_path_rejected(self) -> None:
        document = config()
        del document["paths"]["artifacts"]
        with self.assertRaises(ConfigError):
            parse_config(document)


class TestModelRefEndpointSeparation(unittest.TestCase):
    """Principe 7 : identité logique et lieu d'exécution sont distincts."""

    def test_modelref_has_no_location(self) -> None:
        cfg = parse_config(config())
        ref = cfg.model_roles["coder_main"].reference
        self.assertFalse(hasattr(ref, "url"))
        self.assertFalse(hasattr(ref, "pool_id"))
        self.assertEqual(ref.id, "qwen3-coder:30b-a3b-instruct:q4_k_m")

    def test_endpoint_must_reference_declared_pool(self) -> None:
        document = config()
        document["models"]["endpoints"][0]["pool_id"] = "pool-fantome"
        with self.assertRaises(ConfigError):
            parse_config(document)


class TestLockedPrinciples(unittest.TestCase):
    def test_reference_candidate_is_not_a_declared_winner(self) -> None:
        """Principe 1 : plusieurs candidats, sélection pilotée par benchmark."""
        cfg = parse_config(config())
        role = cfg.model_roles["coder_main"]
        self.assertGreater(len(role.candidates), 1)
        self.assertEqual(role.selection, "benchmark_driven")

    def test_judge_must_differ_from_coder_family(self) -> None:
        """Principe 2 : l'indépendance du Judge est une condition, pas un goût."""
        document = config()
        document["models"]["roles"]["judge"]["candidates"] = [
            {"family": "qwen3-coder", "version": "8b",
             "quantization": "q4_k_m", "capability_class": "judge"}
        ]
        with self.assertRaises(ConfigError) as caught:
            parse_config(document)
        self.assertIn("famille", str(caught.exception))

    def test_autonomous_roles_cannot_be_external(self) -> None:
        """Principe 3 : la boucle autonome ne dépend d'aucun fournisseur externe."""
        document = config()
        document["models"]["roles"]["judge"]["candidates"] = [
            {"family": "some-cloud", "version": "v1",
             "quantization": "external", "capability_class": "judge"}
        ]
        with self.assertRaises(ConfigError) as caught:
            parse_config(document)
        self.assertIn("local", str(caught.exception))

    def test_external_contexts_are_whitelisted(self) -> None:
        document = config()
        document["external"]["allowed_contexts"] = ["interactive", "autonomous_loop"]
        with self.assertRaises(ConfigError) as caught:
            parse_config(document)
        self.assertIn("autonomous_loop", str(caught.exception))

    def test_sandbox_direct_network_rejected(self) -> None:
        """Principe 4 : aucune sortie réseau directe depuis la sandbox."""
        with self.assertRaises(ConfigError):
            parse_config(config(**{"sandbox.network": "host"}))

    def test_local_pool_is_mandatory(self) -> None:
        """Fallback 100 % local : sans pool local, la configuration est invalide."""
        document = config()
        document["compute"]["pools"]["local-gpu"]["location"] = "cloud"
        with self.assertRaises(ConfigError):
            parse_config(document)

    def test_untrusted_pool_may_not_hold_secrets(self) -> None:
        document = config()
        document["compute"]["pools"]["burst"] = {
            "location": "cloud", "trust_level": "untrusted",
            "capabilities": ["inference"], "gpus": [{"model": "A100", "vram_gb": 40}],
        }
        cfg = parse_config(document)
        self.assertFalse(cfg.pools["burst"].may_hold_secrets)
        self.assertTrue(cfg.pools["local-gpu"].may_hold_secrets)
        self.assertIs(cfg.pools["burst"].trust_level, TrustLevel.UNTRUSTED)

    def test_cpp_is_wave_two_with_separate_metrics(self) -> None:
        """Principe 11 : C++ ne contamine pas les benchmarks globaux."""
        cfg = parse_config(config())
        self.assertEqual(cfg.languages["cpp"].wave, 2)
        self.assertTrue(cfg.languages["cpp"].separate_metrics)
        self.assertFalse(cfg.languages["python"].separate_metrics)


class TestEnvSubstitution(unittest.TestCase):
    def test_substitution_and_default(self) -> None:
        env = {"APEX_DATA_ROOT": "/srv/apex"}
        self.assertEqual(substitute_env("${APEX_DATA_ROOT}/w", env), "/srv/apex/w")
        self.assertEqual(substitute_env("${MISSING:-24}", env), "24")

    def test_missing_variable_without_default_fails(self) -> None:
        with self.assertRaises(ConfigError):
            substitute_env("${TOTALLY_ABSENT}", {})

    def test_substitution_is_recursive(self) -> None:
        env = {"H": "1.2.3.4"}
        out = substitute_env({"a": [{"host": "${H}"}]}, env)
        self.assertEqual(out["a"][0]["host"], "1.2.3.4")


class TestBooleanCoercion(unittest.TestCase):
    """Régression : `${VAR:-false}` produit la chaîne "false", pas un booléen.

    Sans coercition stricte, `bool("false")` vaut True et la politique de
    modèles externes s'activait silencieusement — violation directe du
    principe 3, invisible dans les journaux. Bug réel détecté lors du premier
    essai de bout en bout de la CLI en phase 1.
    """

    def test_string_false_disables_external(self) -> None:
        document = config()
        document["external"]["enabled"] = "false"
        self.assertFalse(parse_config(document).external.enabled)

    def test_string_true_enables_external(self) -> None:
        document = config()
        document["external"]["enabled"] = "true"
        self.assertTrue(parse_config(document).external.enabled)

    def test_unknown_boolean_is_an_error_not_a_default(self) -> None:
        document = config()
        document["external"]["enabled"] = "peut-etre"
        with self.assertRaises(ConfigError):
            parse_config(document)

    def test_service_required_flag_coerced(self) -> None:
        document = config()
        document["services"]["postgres"]["required"] = "false"
        self.assertFalse(parse_config(document).services["postgres"].required)



if __name__ == "__main__":
    unittest.main()
