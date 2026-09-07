"""Tests des 10 routes Operator (N+16, `/api/v1/operator/*`) — même patron
qu'`tests/test_api_universal_curriculum.py` : authentification exigée sur
toutes les routes, dégradation propre en `status="unknown"` sans base de
données (jamais un 500), puis un parcours réel via PostgreSQL quand
disponible.
"""

from __future__ import annotations

import os
import time
import unittest

from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)

_GET_PATHS = (
    "/api/v1/operator/status",
    "/api/v1/operator/tasks/x",
    "/api/v1/operator/tasks/x/result",
    "/api/v1/operator/operations",
)
_POST_PATHS = (
    "/api/v1/operator/start",
    "/api/v1/operator/stop",
    "/api/v1/operator/pause",
    "/api/v1/operator/resume",
    "/api/v1/operator/tasks/x/cancel",
)


class TestOperatorRoutesRequireAuthentication(unittest.TestCase):
    def test_get_routes_reject_missing_token(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app

        anonymous = TestClient(create_app())
        for path in _GET_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 401, path)

    def test_post_routes_reject_missing_token(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app

        anonymous = TestClient(create_app())
        for path in _POST_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.post(path).status_code, 401, path)

    def test_submit_task_route_rejects_missing_token(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app

        anonymous = TestClient(create_app())
        response = anonymous.post(
            "/api/v1/operator/tasks",
            json={"title": "t", "specialty": "research", "objective": "o"},
        )
        self.assertEqual(response.status_code, 401)


class TestOperatorRoutesWithoutDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        from apex.api.dependencies import reset_operator_service_for_tests, reset_scheduler_service_for_tests

        reset_scheduler_service_for_tests()
        reset_operator_service_for_tests()
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        from apex.api.dependencies import reset_operator_service_for_tests, reset_scheduler_service_for_tests

        reset_scheduler_service_for_tests()
        reset_operator_service_for_tests()

    def test_start_degrades_to_unknown_never_a_500(self) -> None:
        response = self.client.post("/api/v1/operator/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_status_degrades_to_unknown_never_a_500(self) -> None:
        response = self.client.get("/api/v1/operator/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_submit_task_degrades_to_unknown_never_a_500(self) -> None:
        response = self.client.post(
            "/api/v1/operator/tasks",
            json={"title": "t", "specialty": "research", "objective": "o"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_get_unknown_task_degrades_to_unknown_never_a_500(self) -> None:
        response = self.client.get("/api/v1/operator/tasks/does-not-exist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestOperatorRoutesWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        from apex.api.dependencies import reset_operator_service_for_tests, reset_scheduler_service_for_tests

        reset_scheduler_service_for_tests()
        reset_operator_service_for_tests()
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        from apex.api.dependencies import reset_operator_service_for_tests, reset_scheduler_service_for_tests

        reset_operator_service_for_tests()
        reset_scheduler_service_for_tests()
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def _spawn_research_clone(self) -> str:
        from apex.agents.runtime import AgentRuntime
        from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel

        runtime = AgentRuntime(self._session_factory())
        pool = ComputePool(
            pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
            capabilities=(Capability.CPU_EVAL,),
        )
        clone = runtime.spawn(Subject.RESEARCH, pool=pool, genome_ref="sha256:x", spawned_by=Subject.CORE)
        return clone.clone_id

    def test_full_lifecycle_stopped_start_submit_complete_stop(self) -> None:
        status = self.client.get("/api/v1/operator/status")
        self.assertEqual(status.json()["data"]["state"], "stopped")

        started = self.client.post("/api/v1/operator/start")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "real")
        self.assertEqual(started.json()["data"]["state"], "ready")

        clone_id = self._spawn_research_clone()
        submitted = self.client.post(
            "/api/v1/operator/tasks",
            json={"title": "t", "specialty": "research", "objective": "o", "assigned_agent": clone_id},
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["status"], "real")
        task_id = submitted.json()["data"]["id"]
        self.assertIn(submitted.json()["data"]["state"], ("queued", "running", "review"))

        from apex.api.dependencies import get_scheduler_service

        self.assertTrue(get_scheduler_service().wait_for_mission(task_id, timeout_s=15))

        result = self.client.get(f"/api/v1/operator/tasks/{task_id}/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "real")
        self.assertTrue(result.json()["data"]["ready"])

        stopped = self.client.post("/api/v1/operator/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["data"]["state"], "stopped")

    def test_starting_twice_reports_unknown_not_a_crash(self) -> None:
        self.client.post("/api/v1/operator/start")
        second = self.client.post("/api/v1/operator/start")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "unknown")

    def test_pause_prevents_new_task_progress_resume_allows_it_again(self) -> None:
        self.client.post("/api/v1/operator/start")
        paused = self.client.post("/api/v1/operator/pause")
        self.assertEqual(paused.json()["data"]["state"], "paused")

        clone_id = self._spawn_research_clone()
        submitted = self.client.post(
            "/api/v1/operator/tasks",
            json={"title": "t", "specialty": "research", "objective": "o", "assigned_agent": clone_id},
        )
        task_id = submitted.json()["data"]["id"]
        self.assertEqual(submitted.json()["data"]["state"], "queued")

        resumed = self.client.post("/api/v1/operator/resume")
        self.assertEqual(resumed.json()["data"]["state"], "ready")

        # `wait_for_mission` retourne True IMMÉDIATEMENT pour une mission pas
        # encore suivie (voir sa docstring) — le thread de supervision de
        # production tique toutes les `tick_interval_s` (défaut 1.0s), donc on
        # attend l'état TERMINAL réel plutôt que l'absence de suivi immédiat
        # (même correctif que tests/test_operator_service.py).
        deadline = time.monotonic() + 20
        final_state = None
        while time.monotonic() < deadline:
            final = self.client.get(f"/api/v1/operator/tasks/{task_id}")
            final_state = final.json()["data"]["state"]
            if final_state in ("review", "failed"):
                break
            time.sleep(0.2)
        self.assertIn(final_state, ("review", "failed"))

    def test_recent_operations_reflect_the_lifecycle(self) -> None:
        self.client.post("/api/v1/operator/start")
        ops = self.client.get("/api/v1/operator/operations")
        self.assertEqual(ops.status_code, 200)
        kinds = {e["msg"] for e in ops.json()["data"]}
        self.assertIn("operator.started", kinds)


if __name__ == "__main__":
    unittest.main()
