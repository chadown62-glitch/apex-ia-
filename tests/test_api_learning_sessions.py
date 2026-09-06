"""Tests de `POST /learning-sessions`, `GET /learning-sessions`,
`GET /learning-sessions/{id}`, `POST /learning-sessions/{id}/resume`,
`POST /learning-sessions/{id}/cancel` — passe « Autonomous Multi-Generation
Learning Orchestrator ». Même patron qu'`tests/test_api_population.py` :
sans DB, chaque route dégrade proprement en `"unknown"` (jamais une 500) ;
avec PostgreSQL réel joignable, une session multi-génération réellement
déclenchée via HTTP, retrouvée ensuite via `GET /learning-sessions/{id}`.
"""

from __future__ import annotations

import os
import time
import unittest

from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


class TestLearningSessionRoutesRequireAuthentication(unittest.TestCase):
    def test_start_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        response = anonymous.post("/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 1})
        self.assertEqual(response.status_code, 401)

    def test_start_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        response = client.post("/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 1})
        self.assertEqual(response.status_code, 401)

    def test_list_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/learning-sessions").status_code, 401)

    def test_get_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/learning-sessions/some-id").status_code, 401)

    def test_resume_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.post("/api/v1/learning-sessions/some-id/resume").status_code, 401)

    def test_cancel_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.post("/api/v1/learning-sessions/some-id/cancel").status_code, 401)


class TestLearningSessionRoutesWithoutDatabase(unittest.TestCase):
    """Aucune variable POSTGRES_* définie — chaque route dégrade proprement,
    jamais une 500."""

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

    def test_start_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_list_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/learning-sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_get_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/learning-sessions/some-id")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_resume_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post("/api/v1/learning-sessions/some-id/resume")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_cancel_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post("/api/v1/learning-sessions/some-id/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


class TestLearningSessionRoutesInputValidation(unittest.TestCase):
    """Validation d'entrée — vérifiée AVANT toute tentative de connexion
    DB (fonctionne donc sans PostgreSQL configuré)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_start_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "not-a-real-specialty", "max_generations": 1},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("inconnue", response.json()["detail"])

    def test_start_missing_max_generations_is_a_422(self) -> None:
        response = self.client.post("/api/v1/learning-sessions", json={"specialty": "research"})
        self.assertEqual(response.status_code, 422)

    def test_start_zero_max_generations_is_a_422(self) -> None:
        response = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 0},
        )
        self.assertEqual(response.status_code, 422)

    def test_start_negative_convergence_patience_is_a_422(self) -> None:
        response = self.client.post(
            "/api/v1/learning-sessions",
            json={"specialty": "research", "max_generations": 1, "convergence_patience": -1},
        )
        self.assertEqual(response.status_code, 422)


class TestNoClientControlledIdentity(unittest.TestCase):
    def test_start_endpoint_body_has_no_identity_field(self) -> None:
        import inspect

        from apex.api.schemas import LearningSessionStartIn

        fields = set(LearningSessionStartIn.model_fields)
        for forbidden in ("subject", "requested_by", "actor", "created_by"):
            self.assertNotIn(forbidden, fields)

    def test_resume_and_cancel_accept_no_request_body_at_all(self) -> None:
        import inspect

        from apex.api.routes.learning_sessions import cancel_learning_session, resume_learning_session

        for endpoint in (resume_learning_session, cancel_learning_session):
            signature = inspect.signature(endpoint)
            self.assertEqual(list(signature.parameters), ["session_id"])

    def test_orchestrator_start_session_has_no_identity_parameter(self) -> None:
        import inspect

        from apex.metalearner.learning_orchestrator import LearningOrchestrator

        signature = inspect.signature(LearningOrchestrator.start_session)
        for forbidden in ("subject", "requested_by", "actor", "created_by"):
            self.assertNotIn(forbidden, signature.parameters)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestLearningSessionRoutesWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self.client = api_test_support.authed_client()
        self._promote_a_genome()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def _promote_a_genome(self) -> None:
        from apex.genome.service import GenomeStoreService

        genome = GenomeStoreService(self._session_factory())
        genome.propose(content={"x": 1}, created_by=Subject.HUMAN)
        proposed = genome.list_all()[0]
        genome.promote(proposed.id, requested_by=Subject.HUMAN)

    def _wait_terminal(self, session_id: str, *, timeout_s: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout_s
        body = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]
        while body["status"] in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.05)
            body = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]
        return body

    def test_start_returns_immediately_in_running_state(self) -> None:
        response = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 2},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["status"], "running")
        self.assertEqual(body["data"]["created_by"], Subject.HUMAN.value)
        self._wait_terminal(body["data"]["id"])

    def test_second_start_for_the_same_specialty_is_a_409(self) -> None:
        first = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 2},
        )
        second = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 2},
        )
        self.assertEqual(second.status_code, 409)
        self._wait_terminal(first.json()["data"]["id"])

    def test_full_end_to_end_completes_via_http_and_is_retrievable(self) -> None:
        start = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 2},
        )
        session_id = start.json()["data"]["id"]
        final = self._wait_terminal(session_id)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["stop_reason"], "max_generations_reached")
        self.assertEqual(final["generations_completed"], 2)
        self.assertIsNotNone(final["best_fitness"])

        listed = self.client.get("/api/v1/learning-sessions", params={"specialty": "research"})
        self.assertEqual(listed.status_code, 200)
        self.assertIn(session_id, [s["id"] for s in listed.json()["data"]])

    def test_get_unknown_session_is_a_404(self) -> None:
        response = self.client.get("/api/v1/learning-sessions/not-a-real-session-id")
        self.assertEqual(response.status_code, 404)

    def test_resume_unknown_session_is_a_404(self) -> None:
        response = self.client.post("/api/v1/learning-sessions/not-a-real-session-id/resume")
        self.assertEqual(response.status_code, 404)

    def test_cancel_then_resume_round_trip_over_http(self) -> None:
        start = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 20},
        )
        session_id = start.json()["data"]["id"]
        time.sleep(0.1)
        cancel = self.client.post(f"/api/v1/learning-sessions/{session_id}/cancel")
        self.assertEqual(cancel.status_code, 200)
        cancelled = self._wait_terminal(session_id)
        self.assertEqual(cancelled["status"], "cancelled")

        resume = self.client.post(f"/api/v1/learning-sessions/{session_id}/resume")
        self.assertEqual(resume.status_code, 200)
        self.assertEqual(resume.json()["data"]["status"], "running")
        self._wait_terminal(session_id, timeout_s=5.0)
        self.client.post(f"/api/v1/learning-sessions/{session_id}/cancel")
        self._wait_terminal(session_id)


if __name__ == "__main__":
    unittest.main()
