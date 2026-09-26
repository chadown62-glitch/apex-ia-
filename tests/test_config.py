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
from apex.config.schema import HardwareThresholds, PoolLocation, PoolStatus, TrustLevel
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


class TestRouterScoringPolicy(unittest.TestCase):
    """`RouterScoringPolicy` — passe « Adaptive Model Router V1 ». Même
    discipline que `SchedulerPolicy` : absence de section `routing` dans
    `config.yaml` ⇒ défauts documentés, jamais une erreur."""

    def test_absent_routing_section_uses_documented_defaults(self) -> None:
        cfg = parse_config(config())
        self.assertEqual(cfg.routing.capability_match_weight, 1.0)
        self.assertEqual(cfg.routing.local_preference_weight, 0.5)
        self.assertEqual(cfg.routing.reliability_weight, 0.3)
        self.assertEqual(cfg.routing.latency_weight, 0.2)
        self.assertEqual(cfg.routing.reliability_prior_strength, 0.0)

    def test_custom_weights_are_applied(self) -> None:
        cfg = parse_config(config(routing={"reliability_weight": 0.9, "latency_weight": 0.1}))
        self.assertEqual(cfg.routing.reliability_weight, 0.9)
        self.assertEqual(cfg.routing.latency_weight, 0.1)
        # les poids non fournis gardent leur défaut documenté
        self.assertEqual(cfg.routing.capability_match_weight, 1.0)

    def test_negative_weight_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(config(routing={"reliability_weight": -0.1}))

    def test_zero_weight_is_a_valid_finite_value(self) -> None:
        """`0.0` reste une valeur licite (« désactiver » un axe) —
        distincte de `NaN`/`+inf`/`-inf`, jamais rejetée par erreur par le
        durcissement de validation."""
        cfg = parse_config(config(routing={"latency_weight": 0.0}))
        self.assertEqual(cfg.routing.latency_weight, 0.0)

    def test_nan_weight_rejected_for_every_field(self) -> None:
        """Passe « Hardening » : `NaN < 0` vaut `False` en Python — sans un
        test explicite `math.isfinite`, un poids `NaN` traversait la
        validation en silence. Vérifié sur les 4 champs, pas seulement un."""
        for field_name in (
            "capability_match_weight", "local_preference_weight", "reliability_weight", "latency_weight",
            "reliability_prior_strength",
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ConfigError):
                    parse_config(config(routing={field_name: float("nan")}))

    def test_positive_infinity_weight_rejected_for_every_field(self) -> None:
        """`+inf` est positif (`>= 0`) donc passait l'ancienne garde — un
        poids infini rendrait `ScoreBreakdown.total` infini pour tout
        candidat avec un historique réel."""
        for field_name in (
            "capability_match_weight", "local_preference_weight", "reliability_weight", "latency_weight",
            "reliability_prior_strength",
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ConfigError):
                    parse_config(config(routing={field_name: float("inf")}))

    def test_negative_infinity_weight_rejected_for_every_field(self) -> None:
        """`-inf` était déjà rejeté par `value < 0`, mais avec un message
        d'erreur trompeur (« doit être >= 0 » plutôt que « doit être
        fini ») — vérifié explicitement pour que le message reste correct
        même si la garde de finitude change un jour d'implémentation."""
        for field_name in (
            "capability_match_weight", "local_preference_weight", "reliability_weight", "latency_weight",
            "reliability_prior_strength",
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ConfigError):
                    parse_config(config(routing={field_name: float("-inf")}))

    def test_default_weights_unchanged_by_hardening(self) -> None:
        """Le durcissement de la validation ne doit rien changer au score
        historique par défaut — mêmes 4 valeurs documentées qu'avant cette
        passe."""
        cfg = parse_config(config())
        self.assertEqual(
            (
                cfg.routing.capability_match_weight, cfg.routing.local_preference_weight,
                cfg.routing.reliability_weight, cfg.routing.latency_weight,
            ),
            (1.0, 0.5, 0.3, 0.2),
        )


class TestAutonomousLearningPolicy(unittest.TestCase):
    """`AutonomousLearningPolicy` — passe « Autonomous Learning Trigger ».
    Exigence de sécurité explicite : absence de section `autonomous_learning`
    (ou `enabled` omis) doit TOUJOURS produire `enabled=False`, jamais un
    défaut permissif — installer/mettre à jour APEX ne doit jamais démarrer
    un apprentissage autonome par surprise."""

    def test_absent_section_defaults_to_disabled(self) -> None:
        cfg = parse_config(config())
        self.assertFalse(cfg.autonomous_learning.enabled)
        self.assertEqual(cfg.autonomous_learning.max_concurrent_cycles, 1)
        self.assertEqual(cfg.autonomous_learning.specialty, "research")

    def test_explicitly_enabling_is_honored(self) -> None:
        cfg = parse_config(config(autonomous_learning={"enabled": True, "interval_s": 60}))
        self.assertTrue(cfg.autonomous_learning.enabled)
        self.assertEqual(cfg.autonomous_learning.interval_s, 60.0)

    def test_string_enabled_from_env_substitution_is_coerced_correctly(self) -> None:
        """`${APEX_AUTONOMOUS_LEARNING_ENABLED:-false}` produit la CHAÎNE
        `"false"` après substitution — sans `as_bool`, sa valeur de vérité
        Python (`True`) activerait l'apprentissage autonome par erreur."""
        cfg = parse_config(config(autonomous_learning={"enabled": "false"}))
        self.assertFalse(cfg.autonomous_learning.enabled)
        cfg2 = parse_config(config(autonomous_learning={"enabled": "true"}))
        self.assertTrue(cfg2.autonomous_learning.enabled)

    def test_non_positive_interval_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(config(autonomous_learning={"interval_s": 0}))
        with self.assertRaises(ConfigError):
            parse_config(config(autonomous_learning={"interval_s": -5}))

    def test_max_concurrent_cycles_other_than_one_rejected(self) -> None:
        """Documente honnêtement la limite réelle du code (UN seul cycle
        actif à la fois par processus) plutôt que de tolérer en silence une
        valeur qui mentirait sur ce qui est réellement supporté."""
        with self.assertRaises(ConfigError):
            parse_config(config(autonomous_learning={"max_concurrent_cycles": 2}))

    def test_current_level_below_one_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(config(autonomous_learning={"current_level": 0}))


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

    def test_sandbox_container_image_defaults_when_absent(self) -> None:
        """N+25 : ContainerSandbox a besoin d'une image même si `config.yaml`
        ne la mentionne pas explicitement — jamais None/vide implicite."""
        cfg = parse_config(config())
        self.assertEqual(cfg.sandbox.container_image, "python:3.12-slim")

    def test_sandbox_container_image_is_overridable(self) -> None:
        cfg = parse_config(config(**{"sandbox.container_image": "python:3.13-slim"}))
        self.assertEqual(cfg.sandbox.container_image, "python:3.13-slim")

    def test_sandbox_container_image_rejects_empty_string(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(config(**{"sandbox.container_image": "   "}))

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
        self.assertEqual(thresholds.vram_headroom_fraction, 0.1)

    def test_hardware_thresholds_are_configurable(self) -> None:
        document = config()
        document["gate"] = {
            "hardware": {"min_free_disk_gb": 5, "min_ram_gb": 1, "min_vram_gb": 0, "min_cpu_threads": 1,
                         "vram_headroom_fraction": 0.2}
        }
        cfg = parse_config(document)
        self.assertEqual(cfg.gate.hardware.min_ram_gb, 1.0)
        self.assertEqual(cfg.gate.hardware.min_cpu_threads, 1)
        self.assertEqual(cfg.gate.hardware.vram_headroom_fraction, 0.2)

    def test_vram_headroom_fraction_out_of_range_is_rejected(self) -> None:
        self.assertRaises(ConfigError, HardwareThresholds.parse, {"vram_headroom_fraction": 1.0})
        self.assertRaises(ConfigError, HardwareThresholds.parse, {"vram_headroom_fraction": -0.1})

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
        document["models"]["roles"]["coder_main"]["resident"] = True
        document["models"]["endpoints"] = [
            {
                "pool_id": "local-gpu", "runtime": "ollama", "url": "http://localhost:11434",
                "served_model": document["models"]["roles"]["coder_main"]["candidates"][0]["family"],
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
