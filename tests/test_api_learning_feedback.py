"""Tests de `GET /learning-feedback/{id}`, `GET /weaknesses/{specialty}`,
`GET /weaknesses/{specialty}/history`, `GET /curriculum/{specialty}`,
`GET /population/{specialty}/learning-signals`, `GET /learning-sessions/
{id}/weaknesses` — passe « Judge Feedback -> Weakness -> Curriculum ->
Adaptive Evolution » (N+9). Même patron qu'`tests/test_api_gauntlets.py`."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


class TestLearningFeedbackRoutesRequireAuthentication(unittest.TestCase):
    def test_get_learning_feedback_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/learning-feedback/some-id").status_code, 401)

    def test_get_weaknesses_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/weaknesses/research").status_code, 401)

    def test_get_curriculum_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/curriculum/research").status_code, 401)

    def test_get_learning_signals_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/population/research/learning-signals").status_code, 401)

    def test_get_session_weaknesses_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/learning-sessions/some-id/weaknesses").status_code, 401)


class TestLearningFeedbackRoutesWithoutDatabase(unittest.TestCase):
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

    def test_all_routes_degrade_to_unknown_not_a_500(self) -> None:
        for path in (
            "/api/v1/learning-feedback/x",
            "/api/v1/weaknesses/research",
            "/api/v1/weaknesses/research/history",
            "/api/v1/curriculum/research",
            "/api/v1/population/research/learning-signals",
            "/api/v1/learning-sessions/x/weaknesses",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()["status"], "unknown", path)


class TestLearningFeedbackRoutesInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_weaknesses_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/weaknesses/not-a-real-specialty")
        self.assertEqual(response.status_code, 422)

    def test_curriculum_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/curriculum/not-a-real-specialty")
        self.assertEqual(response.status_code, 422)

    def test_learning_signals_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/population/not-a-real-specialty/learning-signals")
        self.assertEqual(response.status_code, 422)

    def test_weakness_history_unknown_dimension_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/weaknesses/research/history", params={"dimension": "not-a-real-dimension"})
        self.assertEqual(response.status_code, 422)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestLearningFeedbackRoutesWithRealDatabase(unittest.TestCase):
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

    def test_evolve_produces_real_learning_signals_retrievable_via_api(self) -> None:
        evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)

        signals = self.client.get("/api/v1/population/research/learning-signals")
        self.assertEqual(signals.status_code, 200)
        self.assertEqual(signals.json()["status"], "real")
        data = signals.json()["data"]
        self.assertGreater(len(data), 0)
        self.assertIn(data[0]["category"], ("success", "weakness", "failure", "critical_failure", "inconclusive"))

        signal_id = data[0]["id"]
        single = self.client.get(f"/api/v1/learning-feedback/{signal_id}")
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()["data"]["id"], signal_id)

    def test_get_unknown_signal_is_a_404(self) -> None:
        response = self.client.get("/api/v1/learning-feedback/not-a-real-signal-id")
        self.assertEqual(response.status_code, 404)

    def test_weaknesses_and_curriculum_reflect_a_real_forced_security_veto(self) -> None:
        from apex.metalearner.judge_model import (
            JudgeDecision,
            JudgeDimension,
            JudgeFinding,
            JudgeResult,
            JudgeSeverity,
        )
        from apex.metalearner.judges import SecurityJudge

        def always_veto(self_judge, judge_input):
            return JudgeResult(
                judge_id=self_judge.judge_id, judge_version=self_judge.judge_version,
                dimension=self_judge.dimension, decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="véto forcé (test API)",
                findings=(JudgeFinding(dimension=JudgeDimension.SECURITY, severity=JudgeSeverity.CRITICAL, summary="véto forcé"),),
            )

        with patch.object(SecurityJudge, "evaluate", always_veto):
            evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)
        self.assertIsNone(evolve.json()["data"]["champion"])

        weaknesses = self.client.get("/api/v1/weaknesses/research")
        self.assertEqual(weaknesses.status_code, 200)
        data = weaknesses.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["dimension"], "security")
        self.assertEqual(data[0]["severity"], "critical")

        curriculum = self.client.get("/api/v1/curriculum/research")
        self.assertEqual(curriculum.status_code, 200)
        self.assertEqual(len(curriculum.json()["data"]), 1)
        self.assertEqual(curriculum.json()["data"][0]["dimension"], "security")
        # Depuis N+10 : SECURITY recommande un vrai challenge adversarial
        # (AdversarialKind.NETWORK_BOUNDARY_PROBE), jamais un champ Genome
        # inventé pour autant.
        self.assertEqual(curriculum.json()["data"][0]["recommended_mode"], "adversarial")
        self.assertEqual(curriculum.json()["data"][0]["adversarial_kind"], "network_boundary_probe")

    def test_weakness_history_filters_by_dimension(self) -> None:
        self.client.post("/api/v1/population/research/evolve")
        all_history = self.client.get("/api/v1/weaknesses/research/history")
        self.assertEqual(all_history.status_code, 200)
        self.assertGreater(len(all_history.json()["data"]), 0)

        correctness_only = self.client.get(
            "/api/v1/weaknesses/research/history", params={"dimension": "correctness"},
        )
        self.assertEqual(correctness_only.status_code, 200)
        for signal in correctness_only.json()["data"]:
            self.assertEqual(signal["dimension"], "correctness")

    def test_learning_session_weaknesses_delegates_to_its_specialty(self) -> None:
        start = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 1},
        )
        self.assertEqual(start.status_code, 200)
        session_id = start.json()["data"]["id"]

        import time

        deadline = time.monotonic() + 20.0
        status = "running"
        while status in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.1)
            status = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]["status"]

        response = self.client.get(f"/api/v1/learning-sessions/{session_id}/weaknesses")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "real")

    def test_session_weaknesses_unknown_session_is_a_404(self) -> None:
        response = self.client.get("/api/v1/learning-sessions/not-a-real-session-id/weaknesses")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
