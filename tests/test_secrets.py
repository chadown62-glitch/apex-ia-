"""Tests de `SecretBroker`/`EnvSecretBroker` — aucune DB, aucun réseau,
AUCUNE vraie clé API : toutes les valeurs sont des chaînes factices créées
uniquement pour ce test, jamais des credentials réels.
"""

from __future__ import annotations

import unittest

from apex.errors import SecretNotFound
from apex.secrets.model import SecretRef
from apex.secrets.provider import EnvSecretBroker


class TestEnvSecretBrokerResolve(unittest.TestCase):
    def test_resolves_a_present_variable(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_FAKE_PROVIDER_KEY": "not-a-real-key-fixture"})
        self.assertEqual(broker.resolve("fake_provider_key"), "not-a-real-key-fixture")

    def test_key_is_uppercased_and_namespaced(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "test-fixture-value"})
        self.assertEqual(broker.resolve("openai_api_key"), "test-fixture-value")

    def test_missing_variable_raises_secret_not_found(self) -> None:
        broker = EnvSecretBroker(env={})
        with self.assertRaises(SecretNotFound) as ctx:
            broker.resolve("openai_api_key")
        self.assertEqual(ctx.exception.key, "openai_api_key")
        self.assertIn("APEX_SECRET_OPENAI_API_KEY", ctx.exception.source)

    def test_empty_string_is_treated_as_absent_not_a_valid_secret(self) -> None:
        """Une variable définie mais vide n'est jamais un secret valide —
        jamais un « succès » silencieux sur une chaîne vide."""
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": ""})
        with self.assertRaises(SecretNotFound):
            broker.resolve("openai_api_key")

    def test_never_falls_back_to_a_differently_namespaced_variable(self) -> None:
        """Un secret de credential de provider ne doit jamais être confondu
        avec un secret d'infrastructure déjà utilisé ailleurs dans APEX
        (APEX_DB_*_PASSWORD, POSTGRES_PASSWORD, ...)."""
        broker = EnvSecretBroker(env={"OPENAI_API_KEY": "unnamespaced-fixture", "POSTGRES_PASSWORD": "x"})
        with self.assertRaises(SecretNotFound):
            broker.resolve("openai_api_key")

    def test_resolve_ref_delegates_to_resolve(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "test-fixture-value"})
        self.assertEqual(broker.resolve_ref(SecretRef(key="openai_api_key")), "test-fixture-value")


class TestEnvSecretBrokerHas(unittest.TestCase):
    def test_has_is_true_when_present(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": "test-fixture-value"})
        self.assertTrue(broker.has("openai_api_key"))

    def test_has_is_false_when_absent_and_never_raises(self) -> None:
        broker = EnvSecretBroker(env={})
        self.assertFalse(broker.has("openai_api_key"))

    def test_has_is_false_for_an_empty_value(self) -> None:
        broker = EnvSecretBroker(env={"APEX_SECRET_OPENAI_API_KEY": ""})
        self.assertFalse(broker.has("openai_api_key"))


if __name__ == "__main__":
    unittest.main()
