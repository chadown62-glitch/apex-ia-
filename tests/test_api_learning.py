"""Tests de `POST /learning/cycles`, `GET /learning/status`,
`GET /learning/history`, `POST /learning/cycles/cancel` — passe
« Autonomous Learning Trigger + Scheduler/CLI Execution Path ». Même patron
qu'`tests/test_api_evals.py` : sans DB, chaque route dégrade proprement en
`"unknown"` (jamais une 500) ; avec PostgreSQL réel joignable, chemin
`"real"` bout en bout, y compris un cycle réellement déclenché via HTTP et
retrouvé ensuite via `GET /learning/history`.
"""

from __future__ import annotations

import os
import time
import unittest

from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.research_api.dependencies import reset_learning_service_for_tests
from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)

_LEARNING_PATHS = ("/api/v1/learning/status", "/api/v1/learning/history")


class TestLearningRoutesRequireAuthentication(unittest.TestCase):
    def test_status_and_history_reject_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        for path in _LEARNING_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 401)

    def test_status_and_history_reject_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        for path in _LEARNING_PATHS:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)

    def test_start_cycle_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        response = anonymous.post("/api/v1/learning/cycles", json={"specialty": "research"})
        self.assertEqual(response.status_code, 401)

    def test_start_cycle_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        response = client.post("/api/v1/learning/cycles", json={"specialty": "research"})
        self.assertEqual(response.status_code, 401)

    def test_cancel_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.post("/api/v1/learning/cycles/cancel").status_code, 401)


class TestLearningRoutesWithoutDatabase(unittest.TestCase):
    """Aucune variable POSTGRES_* définie — chaque route dégrade
    proprement, jamais une 500."""

    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        reset_learning_service_for_tests()
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        reset_learning_service_for_tests()

    def test_status_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/learning/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_history_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/learning/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_start_cycle_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post("/api/v1/learning/cycles", json={"specialty": "research"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


class TestLearningRoutesInputValidation(unittest.TestCase):
    """Validation d'entrée — vérifiée AVANT toute tentative de connexion
    DB (fonctionne donc sans PostgreSQL configuré)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.post("/api/v1/learning/cycles", json={"specialty": "not-a-real-specialty"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("inconnue", response.json()["detail"])

    def test_unknown_method_is_a_clean_422(self) -> None:
        response = self.client.post(
            "/api/v1/learning/cycles", json={"specialty": "research", "method": "not-a-real-method"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_split_is_a_clean_422(self) -> None:
        response = self.client.post(
            "/api/v1/learning/cycles", json={"specialty": "research", "split": "not-a-real-split"},
        )
        self.assertEqual(response.status_code, 422)

    def test_malformed_body_is_a_clean_422_not_a_500(self) -> None:
        response = self.client.post("/api/v1/learning/cycles", json={})
        self.assertEqual(response.status_code, 422)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestLearningRoutesWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        reset_learning_service_for_tests()
        self.client = api_test_support.authed_client()
        self._promote_a_genome()

    def tearDown(self) -> None:
        reset_learning_service_for_tests()
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

    def _wait_terminal(self, *, timeout_s: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout_s
        status = self.client.get("/api/v1/learning/status").json()["data"]["cycle"]
        while status["state"] in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.2)
            status = self.client.get("/api/v1/learning/status").json()["data"]["cycle"]
        return status

    def test_status_is_idle_before_any_cycle(self) -> None:
        body = self.client.get("/api/v1/learning/status").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["cycle"]["state"], "idle")
        self.assertFalse(body["data"]["enabled"])
        self.assertFalse(body["data"]["scheduler_running"])

    def test_history_is_empty_before_any_cycle(self) -> None:
        body = self.client.get("/api/v1/learning/history").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"], [])

    def test_start_cycle_end_to_end_produces_a_real_retrievable_eval_run(self) -> None:
        response = self.client.post("/api/v1/learning/cycles", json={"specialty": "research", "current_level": 1})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "running")
        cycle_id = body["data"]["cycle_id"]

        final = self._wait_terminal()
        self.assertEqual(final["state"], "completed")
        self.assertEqual(final["cycle_id"], cycle_id)
        self.assertIsNotNone(final["eval_run_id"])
        # Feedback Skill/Genome (voir apex.metalearner.feedback) s'exécute
        # réellement contre PostgreSQL ici, pas seulement SQLite en mémoire —
        # un seul cycle est une preuve insuffisante pour un candidat, mais les
        # deux champs doivent être présents et non provoquer d'échec du cycle.
        self.assertIn("skill_candidate_id", final)
        self.assertIn("genome_candidate_id", final)
        self.assertIsNone(final["skill_candidate_id"])
        self.assertIsNone(final["genome_candidate_id"])

        eval_run = self.client.get(f"/api/v1/eval-runs/{final['eval_run_id']}").json()
        self.assertEqual(eval_run["status"], "real")

        task = self.client.get(f"/api/v1/tasks/{final['task_id']}").json()
        self.assertEqual(task["status"], "real")
        # Toujours HUMAN, jamais un autre sujet — voir la classe de sécurité ci-dessous.
        self.assertEqual(task["data"]["created_by"], "human")

        history = self.client.get("/api/v1/learning/history").json()
        self.assertEqual(history["status"], "real")
        self.assertIn(cycle_id, [e["cycle_id"] for e in history["data"]])

    def test_duplicate_start_while_running_is_a_clean_409(self) -> None:
        first = self.client.post("/api/v1/learning/cycles", json={"specialty": "research", "current_level": 1})
        self.assertEqual(first.status_code, 200)

        second = self.client.post("/api/v1/learning/cycles", json={"specialty": "research", "current_level": 1})
        self.assertEqual(second.status_code, 409)
        self._wait_terminal()

    def test_cancel_on_an_idle_service_is_a_clean_noop(self) -> None:
        response = self.client.post("/api/v1/learning/cycles/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["state"], "idle")


class TestNoClientControlledIdentity(unittest.TestCase):
    """Le corps de `POST /learning/cycles` (`LearningCycleStartIn`) ne
    déclare AUCUN champ d'identité — Pydantic ignore silencieusement tout
    champ additionnel (`subject`/`requested_by`/`actor`/`role`), qui
    n'atteint donc jamais `AutonomousLearningService.start_cycle` (lequel
    ne prend d'ailleurs même pas ce paramètre — `Subject.HUMAN` est en dur,
    voir `apex.metalearner.autonomy`)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_injected_identity_fields_are_silently_ignored_by_the_schema(self) -> None:
        from apex.api.schemas import LearningCycleStartIn

        parsed = LearningCycleStartIn.model_validate(
            {
                "specialty": "research", "subject": "core", "requested_by": "core",
                "actor": "clone", "role": "admin",
            }
        )
        self.assertFalse(hasattr(parsed, "subject"))
        self.assertFalse(hasattr(parsed, "requested_by"))
        self.assertFalse(hasattr(parsed, "actor"))
        self.assertFalse(hasattr(parsed, "role"))

    def test_start_cycle_service_method_has_no_requested_by_parameter(self) -> None:
        """Défense en profondeur : même si un futur appelant interne
        tentait de passer un sujet, la signature ne l'accepte pas —
        vérifié directement sur la fonction, pas seulement supposé."""
        import inspect

        from apex.metalearner.autonomy import AutonomousLearningService

        sig = inspect.signature(AutonomousLearningService.start_cycle)
        self.assertNotIn("requested_by", sig.parameters)


if __name__ == "__main__":
    unittest.main()
