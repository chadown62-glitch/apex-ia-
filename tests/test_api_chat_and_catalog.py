"""Tests des routes `/conversations`, `/projects`, `/agent-templates`,
`/models`, `/providers` — même patron que `tests/test_api.py` :
- sans DB configurée : `/conversations`/`/projects`/`/agent-templates`
  dégradent proprement en `"unknown"`, jamais une 500 ni une fausse donnée
  `"real"`.
- `/models`/`/providers` ne dépendent QUE de `config.yaml` (`apex.config.
  loader.load_config`), jamais de PostgreSQL — donc réellement `"real"`
  même sans DB, à l'image de `/registry`.
- avec PostgreSQL réel joignable : chemin `"real"` bout en bout pour le
  Chat, y compris un message réellement envoyé (contre un `ModelProvider`
  factice fidèle au contrat — jamais un vrai appel réseau payant dans un
  test).
"""

from __future__ import annotations

import os
import unittest

from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


class TestApiWithoutDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_conversations_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/conversations")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "unknown")

    def test_create_conversation_degrades_to_unknown_without_db(self) -> None:
        r = self.client.post("/api/v1/conversations", json={"title": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "unknown")

    def test_projects_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/projects")
        self.assertEqual(r.json()["status"], "unknown")

    def test_agent_templates_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/agent-templates")
        self.assertEqual(r.json()["status"], "unknown")

    def test_models_never_needs_a_database(self) -> None:
        """`/models` lit `config.yaml`, jamais PostgreSQL — réellement
        `"real"` même sans DB configurée, comme `/registry`."""
        r = self.client.get("/api/v1/models")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertGreater(len(body["data"]), 0)
        # Aucune donnée jamais promue AVAILABLE sans sonde réelle.
        for model in body["data"]:
            self.assertIn(model["availability"], ("unknown", "disabled"))

    def test_providers_never_needs_a_database(self) -> None:
        r = self.client.get("/api/v1/providers")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "real")

    def test_discover_never_crashes_even_when_unreachable(self) -> None:
        """Aucun Ollama réel joignable dans ce conteneur — `discover_live`
        doit renvoyer `UNAVAILABLE` empiriquement constaté, jamais une 500
        ni une disponibilité inventée."""
        r = self.client.post("/api/v1/models/discover")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")
        for model in body["data"]:
            self.assertIn(model["availability"], ("unavailable", "disabled"))


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestApiWithRealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()

    def setUp(self) -> None:
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self.client = api_test_support.authed_client()

    def test_conversation_lifecycle_end_to_end(self) -> None:
        created = self.client.post("/api/v1/conversations", json={"title": "Discussion"})
        self.assertEqual(created.json()["status"], "real")
        conversation_id = created.json()["data"]["id"]

        listed = self.client.get("/api/v1/conversations")
        self.assertIn(conversation_id, [c["id"] for c in listed.json()["data"]])

        renamed = self.client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "Renommée"})
        self.assertEqual(renamed.json()["data"]["title"], "Renommée")

        deleted = self.client.delete(f"/api/v1/conversations/{conversation_id}")
        self.assertTrue(deleted.json()["data"])

    def test_project_lifecycle_end_to_end(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "Projet A", "description": "d"})
        self.assertEqual(created.json()["status"], "real")
        listed = self.client.get("/api/v1/projects")
        self.assertIn(created.json()["data"]["id"], [p["id"] for p in listed.json()["data"]])

    def test_agent_template_lifecycle_end_to_end(self) -> None:
        created = self.client.post(
            "/api/v1/agent-templates",
            json={"name": "Codeur", "role": "coder", "description": "d"},
        )
        self.assertEqual(created.json()["status"], "real")
        self.assertEqual(created.json()["data"]["status"], "defined")
        template_id = created.json()["data"]["id"]
        deleted = self.client.delete(f"/api/v1/agent-templates/{template_id}")
        self.assertTrue(deleted.json()["data"])

    def test_agent_template_unknown_role_is_rejected_cleanly(self) -> None:
        r = self.client.post(
            "/api/v1/agent-templates",
            json={"name": "x", "role": "does-not-exist", "description": ""},
        )
        self.assertEqual(r.json()["status"], "unknown")

    def test_send_message_without_a_reachable_provider_reports_error_never_a_500(self) -> None:
        """Aucun Ollama réel joignable ici — le message assistant reflète
        l'échec réel, jamais une 500 ni un texte inventé."""
        created = self.client.post("/api/v1/conversations", json={"title": "c"})
        conversation_id = created.json()["data"]["id"]
        r = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"content": "bonjour"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")  # la requête HTTP a réellement été traitée
        self.assertEqual(body["data"]["assistant_message"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
