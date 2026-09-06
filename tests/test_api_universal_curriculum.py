"""Tests des 8 routes N+11 (« Universal Task + Curriculum Engine », § Phase
20 de la mission) : `GET /curriculum/{specialty}/capabilities`, `GET
/curriculum/{specialty}/plan`, `GET /curriculum/{specialty}/tasks`, `GET
/curriculum/{specialty}/history`, `GET /tasks/{task_id}/provenance`, `GET
/tasks/{task_id}/variants`, `GET /bug-farm/{specialty}`, `GET /specialties`.
Même patron qu'`tests/test_api_capability.py` (N+10)."""

from __future__ import annotations

import os
import unittest

from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)

_ALL_PATHS = (
    "/api/v1/curriculum/research/capabilities",
    "/api/v1/curriculum/research/plan",
    "/api/v1/curriculum/research/tasks",
    "/api/v1/curriculum/research/history",
    "/api/v1/tasks/x/provenance",
    "/api/v1/tasks/x/variants",
    "/api/v1/bug-farm/research",
    "/api/v1/specialties",
)


class TestN11RoutesRequireAuthentication(unittest.TestCase):
    def test_all_routes_reject_missing_token(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app

        anonymous = TestClient(create_app())
        for path in _ALL_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 401, path)


class TestN11RoutesWithoutDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_specialties_never_needs_a_database(self) -> None:
        """`GET /specialties` projette `apex.specialties.registry`, un
        registre en mémoire pur — jamais dégradé, même sans DB."""
        response = self.client.get("/api/v1/specialties")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "real")
        ids = {s["id"] for s in response.json()["data"]}
        self.assertIn("research", ids)

    def test_db_backed_routes_degrade_to_unknown_not_a_500(self) -> None:
        # `/specialties` et `/curriculum/{specialty}/capabilities` ne
        # touchent JAMAIS de DB (registres en mémoire purs,
        # `apex.specialties.registry`/`apex.taskgen.registry`) — exclus
        # légitimement, même discipline que `/specialties` lui-même.
        db_independent = {"/api/v1/specialties", "/api/v1/curriculum/research/capabilities"}
        for path in _ALL_PATHS:
            if path in db_independent:
                continue
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (200, 404), path)
                if response.status_code == 200:
                    self.assertEqual(response.json()["status"], "unknown", path)


class TestN11RoutesInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_unknown_specialty_is_a_clean_422(self) -> None:
        for path in (
            "/api/v1/curriculum/not-a-real-specialty/capabilities",
            "/api/v1/curriculum/not-a-real-specialty/plan",
            "/api/v1/curriculum/not-a-real-specialty/tasks",
            "/api/v1/curriculum/not-a-real-specialty/history",
            "/api/v1/bug-farm/not-a-real-specialty",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422, path)

    def test_capabilities_of_an_unregistered_specialty_reports_unsupported_not_an_error(self) -> None:
        """§5 — `code-intelligence` est une spécialité VALIDE (registre) mais
        SANS `SpecialtyTaskProvider` réel : `supported=False`, capacités
        vides, jamais une erreur ni une capacité inventée."""
        response = self.client.get("/api/v1/curriculum/code-intelligence/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "real")
        self.assertFalse(response.json()["data"]["supported"])
        self.assertEqual(response.json()["data"]["capabilities"], {})


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestN11RoutesWithRealDatabase(unittest.TestCase):
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

    def test_research_capability_matrix_declares_all_seven_kinds_supported(self) -> None:
        response = self.client.get("/api/v1/curriculum/research/capabilities")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["supported"])
        expected_kinds = (
            "normal", "edge_case", "adversarial", "security", "generalization", "regression", "bug_farm",
        )
        for kind in expected_kinds:
            self.assertTrue(data["capabilities"].get(kind), kind)

    def test_forced_security_veto_produces_real_curriculum_data_across_every_n11_route(self) -> None:
        from unittest.mock import patch

        from apex.metalearner.judge_model import (
            JudgeDecision,
            JudgeDimension,
            JudgeFinding,
            JudgeResult,
            JudgeSeverity,
        )
        from apex.metalearner.judges import SecurityJudge

        def always_veto(self_judge, judge_input):
            finding = JudgeFinding(
                dimension=JudgeDimension.SECURITY, severity=JudgeSeverity.CRITICAL, summary="véto forcé",
            )
            return JudgeResult(
                judge_id=self_judge.judge_id, judge_version=self_judge.judge_version,
                dimension=self_judge.dimension, decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="véto forcé (test API N+11)", findings=(finding,),
            )

        with patch.object(SecurityJudge, "evaluate", always_veto):
            evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)

        plan = self.client.get("/api/v1/curriculum/research/plan")
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()["status"], "real")
        kinds = {item["kind"] for item in plan.json()["data"]["items"]}
        self.assertIn("security", kinds)

        tasks = self.client.get("/api/v1/curriculum/research/tasks")
        self.assertEqual(tasks.status_code, 200)
        self.assertGreaterEqual(len(tasks.json()["data"]), 1)
        task_id = tasks.json()["data"][0]["id"]

        history = self.client.get("/api/v1/curriculum/research/history")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(len(history.json()["data"]), 1)
        history_task_ids = {c["task_id"] for c in history.json()["data"]}
        self.assertIn(task_id, history_task_ids)

        provenance = self.client.get(f"/api/v1/tasks/{task_id}/provenance")
        self.assertEqual(provenance.status_code, 200)
        self.assertEqual(provenance.json()["status"], "real")
        self.assertIsNotNone(provenance.json()["data"]["weakness_id"])

        variants = self.client.get(f"/api/v1/tasks/{task_id}/variants")
        self.assertEqual(variants.status_code, 200)

        bug_farm = self.client.get("/api/v1/bug-farm/research")
        self.assertEqual(bug_farm.status_code, 200)
        bugs = self.client.get("/api/v1/bugs/research")
        self.assertEqual(bug_farm.json()["data"], bugs.json()["data"])

    def test_task_provenance_unknown_task_is_a_404(self) -> None:
        response = self.client.get("/api/v1/tasks/not-a-real-task/provenance")
        self.assertEqual(response.status_code, 404)
