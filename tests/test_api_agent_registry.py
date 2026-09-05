"""Tests des routes `/configured-agents`, `/agent-teams`,
`POST /missions/{id}/run` — même patron que `tests/test_api_chat_and_
catalog.py`. `/missions/{id}/run` est testé contre PostgreSQL réel avec un
`ModelProvider` factice (jamais un vrai appel réseau payant) : impossible de
prouver la sélection réelle sans persister réellement une Mission/un Agent.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

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

    def test_configured_agents_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/configured-agents")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "unknown")

    def test_create_configured_agent_degrades_to_unknown_without_db(self) -> None:
        r = self.client.post("/api/v1/configured-agents", json={"name": "x", "role": "coder"})
        self.assertEqual(r.json()["status"], "unknown")

    def test_run_mission_degrades_to_unknown_without_db(self) -> None:
        r = self.client.post("/api/v1/missions/does-not-exist/run")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "unknown")


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

    def test_agent_lifecycle_end_to_end(self) -> None:
        created = self.client.post(
            "/api/v1/configured-agents",
            json={"name": "APEX Coder", "role": "coder", "model_role": "fast", "permissions": ["execute"]},
        )
        self.assertEqual(created.json()["status"], "real")
        self.assertEqual(created.json()["data"]["status"], "idle")
        agent_id = created.json()["data"]["id"]

        listed = self.client.get("/api/v1/configured-agents")
        self.assertIn(agent_id, [a["id"] for a in listed.json()["data"]])

        updated = self.client.patch(f"/api/v1/configured-agents/{agent_id}", json={"name": "Renamed"})
        self.assertEqual(updated.json()["data"]["name"], "Renamed")

        deleted = self.client.delete(f"/api/v1/configured-agents/{agent_id}")
        self.assertTrue(deleted.json()["data"])

    def test_no_tool_or_permission_by_default_through_the_api(self) -> None:
        created = self.client.post("/api/v1/configured-agents", json={"name": "n", "role": "researcher"})
        self.assertEqual(created.json()["data"]["tools"], [])
        self.assertEqual(created.json()["data"]["permissions"], [])

    def test_unknown_role_is_rejected_cleanly(self) -> None:
        r = self.client.post("/api/v1/configured-agents", json={"name": "x", "role": "does-not-exist"})
        self.assertEqual(r.json()["status"], "unknown")

    def test_agent_team_lifecycle(self) -> None:
        agent = self.client.post("/api/v1/configured-agents", json={"name": "a1", "role": "coder"}).json()["data"]
        team = self.client.post(
            "/api/v1/agent-teams", json={"name": "Dev Team", "description": "d", "agent_ids": [agent["id"]]},
        )
        self.assertEqual(team.json()["status"], "real")
        listed = self.client.get("/api/v1/agent-teams")
        self.assertIn(team.json()["data"]["id"], [t["id"] for t in listed.json()["data"]])

    def test_full_chain_mission_run_via_agent_with_a_fake_provider(self) -> None:
        """UI -> API `POST /missions/{id}/run` -> `AgentRegistryService.
        run_execution` -> `select_model` -> `generate_with_fallback` ->
        provider (double fidèle, injecté au niveau service — jamais un vrai
        appel réseau payant dans un test) -> réponse réelle, persistée."""
        agent = self.client.post(
            "/api/v1/configured-agents",
            json={"name": "APEX Coder", "role": "coder", "model_role": "fast", "permissions": ["execute"]},
        ).json()["data"]

        mission = self.client.post(
            "/api/v1/missions",
            json={
                "title": "Factorielle", "specialty": "research",
                "objective": "Écris une fonction Python qui calcule la factorielle.",
                "agent_id": agent["id"],
            },
        ).json()["data"]
        self.assertEqual(mission["agent_id"], agent["id"])

        from apex.models.model import GenerationRequest, GenerationResult
        from apex.obs.status import Measurement

        def fake_generate(self, request: GenerationRequest) -> GenerationResult:
            return GenerationResult(
                text="réponse réelle via API", model_used=request.endpoint.served_model,
                tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
                finish_reason="stop", elapsed_s=0.001, facts={},
            )

        from apex.models.ollama import OllamaProvider

        with mock.patch.object(OllamaProvider, "detect") as mock_detect, \
                mock.patch.object(OllamaProvider, "generate", fake_generate):
            from apex.models.model import ModelCapabilityReport
            from apex.obs.capability import CapabilityStatus

            mock_detect.return_value = ModelCapabilityReport(
                status=CapabilityStatus.AVAILABLE, backend="ollama", detail="double de test",
            )
            r = self.client.post(f"/api/v1/missions/{mission['id']}/run")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["status"], "completed")
        self.assertEqual(body["data"]["result"], "réponse réelle via API")
        self.assertIsNotNone(body["data"]["model_id"])
        self.assertIsNotNone(body["data"]["provider_id"])

        final_mission = self.client.get(f"/api/v1/missions/{mission['id']}").json()["data"]
        self.assertEqual(final_mission["state"], "review")
        self.assertEqual(final_mission["result"], "réponse réelle via API")

        executions = self.client.get(f"/api/v1/configured-agents/{agent['id']}/executions").json()["data"]
        self.assertEqual(len(executions), 1)
        mission_executions = self.client.get(f"/api/v1/missions/{mission['id']}/executions").json()["data"]
        self.assertEqual(len(mission_executions), 1)

    def test_run_without_a_reachable_ollama_reports_a_real_failure_never_a_fake_success(self) -> None:
        """Aucun Ollama réel joignable ici — le résultat reflète l'échec
        réel, jamais un texte inventé."""
        agent = self.client.post(
            "/api/v1/configured-agents",
            json={"name": "n", "role": "coder", "model_role": "fast", "permissions": ["execute"]},
        ).json()["data"]
        mission = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o", "agent_id": agent["id"]},
        ).json()["data"]

        r = self.client.post(f"/api/v1/missions/{mission['id']}/run")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["status"], "failed")
        self.assertIsNone(body["data"]["result"])

    def test_run_without_an_assigned_agent_is_refused_cleanly(self) -> None:
        mission = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"},
        ).json()["data"]
        r = self.client.post(f"/api/v1/missions/{mission['id']}/run")
        self.assertEqual(r.json()["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
