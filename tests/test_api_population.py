"""Tests de `GET /population/{specialty}`, `POST /population/{specialty}/
evolve` — passe « Real Clone Population + Evolution Loop ». Même patron
qu'`tests/test_api_learning.py` : sans DB, chaque route dégrade proprement
en `"unknown"` (jamais une 500) ; avec PostgreSQL réel joignable, une
génération complète réellement déclenchée via HTTP, retrouvée ensuite via
`GET /population/{specialty}`.
"""

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


class TestPopulationRoutesRequireAuthentication(unittest.TestCase):
    def test_get_population_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.get("/api/v1/population/research").status_code, 401)

    def test_get_population_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        self.assertEqual(client.get("/api/v1/population/research").status_code, 401)

    def test_evolve_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        self.assertEqual(anonymous.post("/api/v1/population/research/evolve").status_code, 401)

    def test_evolve_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        self.assertEqual(client.post("/api/v1/population/research/evolve").status_code, 401)


class TestPopulationRoutesWithoutDatabase(unittest.TestCase):
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

    def test_get_population_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.get("/api/v1/population/research")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")

    def test_evolve_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


class TestPopulationRoutesInputValidation(unittest.TestCase):
    """Validation d'entrée — vérifiée AVANT toute tentative de connexion
    DB (fonctionne donc sans PostgreSQL configuré)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_get_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/population/not-a-real-specialty")
        self.assertEqual(response.status_code, 422)
        self.assertIn("inconnue", response.json()["detail"])

    def test_evolve_unknown_specialty_is_a_clean_422(self) -> None:
        response = self.client.post("/api/v1/population/not-a-real-specialty/evolve")
        self.assertEqual(response.status_code, 422)


class TestNoClientControlledIdentity(unittest.TestCase):
    def test_evolve_endpoint_accepts_no_body_at_all(self) -> None:
        """Aucun champ d'identité n'est même POSSIBLE : la route ne
        déclare aucun corps de requête (voir apex/api/routes/population.py)
        — un client ne peut structurellement pas injecter `subject`/
        `requested_by`, contrairement à `POST /learning/cycles` qui doit
        explicitement les ignorer (payload avec corps)."""
        import inspect

        from apex.research_api.routes.population import evolve_population

        signature = inspect.signature(evolve_population)
        self.assertEqual(list(signature.parameters), ["specialty"])

    def test_evolve_generation_service_method_has_no_requested_by_parameter(self) -> None:
        import inspect

        from apex.metalearner.evolution import PopulationEvolutionService

        signature = inspect.signature(PopulationEvolutionService.evolve_generation)
        for forbidden in ("requested_by", "subject", "actor"):
            self.assertNotIn(forbidden, signature.parameters)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestPopulationRoutesWithRealDatabase(unittest.TestCase):
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

    def test_population_is_empty_before_any_generation(self) -> None:
        response = self.client.get("/api/v1/population/research")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["members"], [])
        self.assertEqual(body["data"]["latest_generation"], -1)
        self.assertIsNone(body["data"]["champion"])

    def test_evolve_end_to_end_produces_a_real_retrievable_population(self) -> None:
        response = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["generation"], 0)
        self.assertGreater(len(body["data"]["members"]), 0)
        self.assertIsNotNone(body["data"]["champion"])
        # `created_by` d'un membre reste toujours CORE — jamais négociable
        # depuis le corps de la requête (qui n'existe même pas ici).
        for member in body["data"]["members"]:
            self.assertIn(member["status"], ("champion", "elite", "hibernating", "retired"))

        readback = self.client.get("/api/v1/population/research")
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json()["data"]["latest_generation"], 0)
        self.assertEqual(len(readback.json()["data"]["members"]), len(body["data"]["members"]))

    def test_second_evolve_call_advances_to_generation_one(self) -> None:
        self.client.post("/api/v1/population/research/evolve")
        second = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(second.json()["data"]["generation"], 1)


if __name__ == "__main__":
    unittest.main()
