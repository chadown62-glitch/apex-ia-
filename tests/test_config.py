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
from apex.config.schema import (
    HardwareThresholds,
    ModelEndpoint,
    ModelRef,
    PoolLocation,
    PoolStatus,
    TrustLevel,
    model_ref_matches_endpoint,
)
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
            # Forme réelle de `config.yaml` : "<family>:<version>-<quant>"
            # (voir `apex.config.schema.model_ref_matches_endpoint`) — pas
            # une version tronquée qui ne matcherait plus que par un
            # accident de préfixe de famille.
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:30b-a3b-instruct-q4_K_M", "max_context": 65536, "vram_gb": 18.0}
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


class TestModelRefEndpointMatching(unittest.TestCase):
    """`model_ref_matches_endpoint` — appariement par identité réelle
    (family + version), pas par simple préfixe de famille. Corrige un bug
    architectural précis : un rôle résident et un rôle non résident de la
    même famille (deux tailles du même modèle) étaient confondus, gonflant
    à tort le calcul de VRAM résidente avec l'endpoint du rôle non résident.
    """

    def _endpoint(self, served_model: str, vram_gb: float) -> ModelEndpoint:
        return ModelEndpoint(
            pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
            served_model=served_model, max_context=32768, vram_gb=vram_gb,
        )

    def test_same_family_different_version_are_not_the_same_endpoint(self) -> None:
        """Échoue avec l'ancien matching par préfixe de famille seule
        (`served_model.startswith(ref.family)`), passe avec family+version."""
        coder_main_ref = ModelRef(
            family="qwen3-coder", version="30b-a3b-instruct",
            quantization="q4_k_m", capability_class="coder",
        )
        fast_ref = ModelRef(
            family="qwen3-coder", version="8b",
            quantization="q4_k_m", capability_class="fast",
        )
        coder_main_endpoint = self._endpoint("qwen3-coder:30b-a3b-instruct-q4_K_M", 18.0)
        fast_endpoint = self._endpoint("qwen3-coder:8b-q4_K_M", 5.0)

        # L'ancien comportement (préfixe de famille seule) aurait fait
        # matcher CHACUN des deux endpoints contre CHACUNE des deux
        # références, puisque les deux partagent la famille "qwen3-coder".
        old_family_only_would_match = coder_main_endpoint.served_model.startswith(fast_ref.family)
        self.assertTrue(
            old_family_only_would_match,
            "prémisse du test : les deux doivent partager la même famille",
        )

        self.assertTrue(model_ref_matches_endpoint(coder_main_ref, coder_main_endpoint))
        self.assertTrue(model_ref_matches_endpoint(fast_ref, fast_endpoint))
        # Le point précis du correctif : chaque référence ne matche QUE son
        # propre endpoint, jamais celui de l'autre candidat de la famille.
        self.assertFalse(model_ref_matches_endpoint(fast_ref, coder_main_endpoint))
        self.assertFalse(model_ref_matches_endpoint(coder_main_ref, fast_endpoint))

    def test_family_prefix_boundary_requires_a_real_separator(self) -> None:
        """Une version plus courte ne doit jamais matcher, par accident de
        préfixe, un `served_model` qui la prolonge sans séparateur — ex.
        version "8b" contre un endpoint "8b2-instruct" (un AUTRE modèle)."""
        short_ref = ModelRef(
            family="qwen3-coder", version="8b", quantization="q4_k_m", capability_class="fast",
        )
        unrelated_endpoint = self._endpoint("qwen3-coder:8b2-instruct-q4_K_M", 6.0)
        self.assertFalse(model_ref_matches_endpoint(short_ref, unrelated_endpoint))

    def test_exact_served_model_without_quantization_suffix_matches(self) -> None:
        """Convention réelle observée dans `config.yaml` : certains tags
        Ollama ne portent aucun suffixe de quantification du tout (ex.
        `qwen2.5-coder:7b-instruct`, `bge-m3:latest`)."""
        ref = ModelRef(
            family="bge-m3", version="latest", quantization="fp16", capability_class="embed",
        )
        endpoint = self._endpoint("bge-m3:latest", 0.6)
        self.assertTrue(model_ref_matches_endpoint(ref, endpoint))

    def test_mandatory_case_coder_main_30b_vs_fast_8b_resident_vram(self) -> None:
        """CAS DE TEST OBLIGATOIRE : `coder_main` (30B, ~18 Go, non résident)
        et `fast` (8B, ~5 Go, résident) partagent la famille `qwen3-coder`.
        `embed` (résident, ~0.6 Go) est d'une famille différente. Le calcul
        résident doit valoir ~5.6 Go et NE DOIT PAS inclure les 18 Go du
        `coder_main` — reproduit exactement le défaut rapporté (23.6 Go
        calculés à tort pour 8 Go réellement disponibles)."""
        document = config()
        document["models"]["roles"] = {
            "coder_main": {
                "selection": "benchmark_driven",
                "resident": False,
                "candidates": [
                    {"family": "qwen3-coder", "version": "30b-a3b-instruct",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                ],
            },
            "fast": {
                "selection": "static_prior",
                "resident": True,
                "candidates": [
                    {"family": "qwen3-coder", "version": "8b",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
            "embed": {
                "selection": "static_prior",
                "resident": True,
                "candidates": [
                    {"family": "bge-m3", "version": "latest",
                     "quantization": "fp16", "capability_class": "embed"},
                ],
            },
        }
        document["models"]["endpoints"] = [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:30b-a3b-instruct-q4_K_M", "max_context": 65536, "vram_gb": 18.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:8b-q4_K_M", "max_context": 32768, "vram_gb": 5.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "bge-m3:latest", "max_context": 8192, "vram_gb": 0.6},
        ]
        # Pool ACTIVE dimensionné comme la machine cible du défaut rapporté
        # (RTX 4060 Ti, 8 Go) — 23.6 Go y dépasserait, 5.6 Go y tient.
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8

        cfg = parse_config(document)  # ne doit PAS lever : 5.6 Go <= 8 Go

        resident = [
            e
            for e in cfg.endpoints
            for role in cfg.model_roles.values()
            if role.resident and model_ref_matches_endpoint(role.reference, e)
        ]
        needed = round(sum(e.effective_vram_gb or 0.0 for e in resident), 1)
        self.assertEqual(needed, 5.6)
        self.assertNotIn(
            "qwen3-coder:30b-a3b-instruct-q4_K_M", {e.served_model for e in resident},
            "l'endpoint du coder_main NON résident ne doit jamais entrer dans le calcul résident",
        )


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


class TestCapabilityAwareArchitecture(unittest.TestCase):
    """Le hardware de la machine qui EXÉCUTE APEX (dev, cible, prod) est une
    variable d'environnement, jamais une contrainte architecturale codée en
    dur. Ces tests garantissent que : (1) les seuils du Gate sont un choix de
    déploiement configurable, pas une limite fixe ; (2) un ComputePool
    `status: target` (machine future, pas encore construite) ne peut jamais
    se faire passer pour une disponibilité réelle aujourd'hui, dans aucun des
    calculs qui comptent (fallback local obligatoire, capacité VRAM résidente)."""

    def test_hardware_thresholds_default_to_the_previous_hardcoded_values(self) -> None:
        """Aucune limite existante n'a changé — seulement sa configurabilité."""
        thresholds = HardwareThresholds.parse({})
        self.assertEqual(thresholds.min_free_disk_gb, 150.0)
        self.assertEqual(thresholds.min_ram_gb, 24.0)
        self.assertEqual(thresholds.min_vram_gb, 20.0)
        self.assertEqual(thresholds.min_cpu_threads, 8)

    def test_hardware_thresholds_are_configurable(self) -> None:
        document = config()
        document["gate"] = {
            "hardware": {"min_free_disk_gb": 5, "min_ram_gb": 1, "min_vram_gb": 0, "min_cpu_threads": 1}
        }
        cfg = parse_config(document)
        self.assertEqual(cfg.gate.hardware.min_ram_gb, 1.0)
        self.assertEqual(cfg.gate.hardware.min_cpu_threads, 1)

    def test_pool_status_defaults_to_active(self) -> None:
        cfg = parse_config(config())
        self.assertIs(cfg.pools["local-gpu"].status, PoolStatus.ACTIVE)

    def test_pool_status_target_is_parseable(self) -> None:
        document = config()
        document["compute"]["pools"]["future-machine"] = {
            "location": "local", "trust_level": "trusted", "status": "target",
            "capabilities": ["inference"], "gpus": [{"model": "TBD", "vram_gb": 200}],
            "cpu_cores": 256, "ram_gb": 1024,
        }
        cfg = parse_config(document)
        self.assertIs(cfg.pools["future-machine"].status, PoolStatus.TARGET)

    def test_a_target_only_local_pool_does_not_satisfy_the_local_fallback_requirement(self) -> None:
        """Principe 6 : le fallback 100% local doit être une disponibilité
        RÉELLE. Une machine visée, pas encore construite, ne compte pas."""
        document = config()
        document["compute"]["pools"]["local-gpu"]["status"] = "target"
        with self.assertRaises(ConfigError):
            parse_config(document)

    def test_target_pool_vram_never_inflates_real_resident_capacity(self) -> None:
        """Un modèle résident dimensionné pour la VRAM d'une machine cible
        future ne doit jamais valider contre la VRAM du pool ACTIVE réel."""
        document = config()
        # local-gpu (ACTIVE) déclare 24 Go — voir BASE. On ajoute une cible
        # bien plus grande qui ne doit compter pour rien ici.
        document["compute"]["pools"]["future-machine"] = {
            "location": "local", "trust_level": "trusted", "status": "target",
            "capabilities": ["inference"], "gpus": [{"model": "TBD", "vram_gb": 200}],
        }
        # coder_main est résident et sa référence vaut ~qwen3-coder — on force
        # un vram_gb d'endpoint qui tient dans le pool ACTIVE (24 Go) mais pas
        # dans le pool ACTIVE si le calcul incluait par erreur la cible.
        # `served_model` doit correspondre par family+version (voir
        # `model_ref_matches_endpoint`) — un simple préfixe de famille ne
        # suffit plus à faire matcher intentionnellement cet endpoint.
        document["models"]["roles"]["coder_main"]["resident"] = True
        reference = document["models"]["roles"]["coder_main"]["candidates"][0]
        document["models"]["endpoints"] = [
            {
                "pool_id": "local-gpu", "runtime": "ollama", "url": "http://localhost:11434",
                "served_model": f"{reference['family']}:{reference['version']}-q4_K_M",
                "max_context": 8192, "vram_gb": 20,
            }
        ]
        cfg = parse_config(document)  # 20 Go tient dans les 24 Go ACTIVE : ne doit pas lever
        self.assertIs(cfg.pools["future-machine"].status, PoolStatus.TARGET)

        # Maintenant on demande 100 Go — ne tient ni dans les 24 Go ACTIVE ni
        # nulle part de légitime : doit échouer, PAS réussir en piochant dans
        # la capacité de la cible.
        document["models"]["endpoints"][0]["vram_gb"] = 100
        with self.assertRaises(ConfigError):
            parse_config(document)


if __name__ == "__main__":
    unittest.main()
