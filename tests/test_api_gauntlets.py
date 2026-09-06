"""Tests de `GET /gauntlets/{id}`, `GET /population/{specialty}/gauntlets`
— passe « Strict Judge System + Gauntlet Learning Loop » (N+8). Même
patron qu'`tests/test_api_population.py` : sans DB, chaque route dégrade
proprement en `"unknown"` (jamais une 500) ; avec PostgreSQL réel
joignable, un gauntlet RÉELLEMENT produit par `POST /population/{specialty}
/evolve` est retrouvé via ces deux routes."""

from __future__ import annotations

import os
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


class TestGauntletRoutesRequireAuthentication(unittest.TestCase):
    def test_get_gauntlet_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/gauntlets/some-id").status_code, 401)

    def test_list_gauntlets_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/population/research/gauntlets").status_code, 401)


class TestGauntletRoutesWithoutDatabase(unittest.TestCase):
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

    def test_get_gauntlet_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/gauntlets/some-id")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_list_gauntlets_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/population/research/gauntlets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


class TestGauntletRoutesInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_list_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/population/not-a-real-specialty/gauntlets")
        self.assertEqual(response.status_code, 422)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestGauntletRoutesWithRealDatabase(unittest.TestCase):
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

    def test_evolve_produces_real_gauntlets_retrievable_by_member_and_specialty(self) -> None:
        evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)
        members = evolve.json()["data"]["members"]
        self.assertGreater(len(members), 0)
        member_id = members[0]["id"]

        by_member = self.client.get("/api/v1/population/research/gauntlets", params={"member_id": member_id})
        self.assertEqual(by_member.status_code, 200)
        self.assertEqual(by_member.json()["status"], "real")
        gauntlets = by_member.json()["data"]
        self.assertGreaterEqual(len(gauntlets), 1)
        self.assertEqual(gauntlets[0]["member_id"], member_id)
        self.assertIn(gauntlets[0]["decision"], ("pass", "reject", "inconclusive"))
        self.assertGreater(len(gauntlets[0]["judge_results"]), 0)

        by_specialty = self.client.get("/api/v1/population/research/gauntlets")
        self.assertEqual(by_specialty.status_code, 200)
        self.assertGreaterEqual(len(by_specialty.json()["data"]), len(members))

        gauntlet_id = gauntlets[0]["id"]
        single = self.client.get(f"/api/v1/gauntlets/{gauntlet_id}")
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()["data"]["id"], gauntlet_id)

    def test_champion_candidate_has_a_full_stage_gauntlet(self) -> None:
        evolve = self.client.post("/api/v1/population/research/evolve")
        champion = evolve.json()["data"]["champion"]
        self.assertIsNotNone(champion)

        by_member = self.client.get("/api/v1/population/research/gauntlets", params={"member_id": champion["id"]})
        stages = {g["stage"] for g in by_member.json()["data"]}
        self.assertIn("full", stages)

    def test_get_unknown_gauntlet_is_a_404(self) -> None:
        response = self.client.get("/api/v1/gauntlets/not-a-real-gauntlet-id")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
