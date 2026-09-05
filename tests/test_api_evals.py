"""Tests de `GET /tasks`, `GET /eval-runs`, `GET /weakness/{specialty}`,
`GET /self-model/{specialty}`, `POST /experiment-cycles` — passe « API
Évaluation/Apprentissage ». Même patron que `tests/test_api.py`/`tests/
test_api_agent_registry.py` : sans DB, chaque route dégrade proprement en
`"unknown"` (jamais une 500) ; avec PostgreSQL réel joignable, chemin
`"real"` bout en bout, y compris un `run_experiment_cycle` réellement
déclenché via HTTP et son `EvalRun` retrouvé ensuite via `GET /eval-runs`.

L'authentification (`apex.api.auth.require_api_token`, déjà câblée
uniformément par `apex.api.app.create_app`) n'est jamais redupliquée ici :
ce fichier ne fait que vérifier qu'elle s'applique bien à ces 7 routes
précises, exactement comme `tests/test_api_auth.py` le fait déjà pour les
routes existantes.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.api.app import create_app
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.metalearner.cycle import run_experiment_cycle
from apex.policy.model import Subject
from tests import api_test_support
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)

_READ_PATHS = (
    "/api/v1/tasks",
    "/api/v1/tasks/does-not-exist",
    "/api/v1/eval-runs",
    "/api/v1/eval-runs/does-not-exist",
    "/api/v1/weakness/research",
    "/api/v1/self-model/research",
)


class TestEvalsRoutesRequireAuthentication(unittest.TestCase):
    """Même frontière uniforme que le reste de l'API (`apex.api.auth.
    require_api_token`) — vérifiée explicitement sur les 7 nouvelles routes,
    pas seulement supposée héritée de la boucle d'inclusion."""

    def test_every_read_route_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        for path in _READ_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 401)

    def test_every_read_route_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        for path in _READ_PATHS:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)

    def test_every_read_route_accepts_the_correct_token(self) -> None:
        client = api_test_support.authed_client()
        for path in _READ_PATHS:
            with self.subTest(path=path):
                # 200 dans tous les cas : sans DB configurée, la route
                # dégrade en "unknown" (voir TestEvalsRoutesWithoutDatabase)
                # plutôt que de refuser l'authentification — la preuve
                # recherchée ici est seulement "le jeton correct passe".
                self.assertEqual(client.get(path).status_code, 200)

    def test_experiment_cycles_rejects_missing_token(self) -> None:
        anonymous = TestClient(create_app())
        response = anonymous.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": "x"},
        )
        self.assertEqual(response.status_code, 401)

    def test_experiment_cycles_rejects_wrong_token(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-value"})
        response = client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": "x"},
        )
        self.assertEqual(response.status_code, 401)


class TestEvalsRoutesWithoutDatabase(unittest.TestCase):
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

    def test_reads_degrade_to_unknown_not_a_500(self) -> None:
        for path in _READ_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "unknown")

    def test_experiment_cycle_degrades_to_unknown_not_a_500(self) -> None:
        response = self.client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": "does-not-exist"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")


class TestEvalsRoutesInputValidation(unittest.TestCase):
    """Validation d'entrée — vérifiée AVANT toute tentative de connexion DB
    (fonctionne donc sans PostgreSQL configuré, comme les tests ci-dessus)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_unknown_specialty_on_weakness_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/weakness/not-a-real-specialty")
        self.assertEqual(response.status_code, 422)
        self.assertIn("inconnue", response.json()["detail"])

    def test_unknown_specialty_on_self_model_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/self-model/not-a-real-specialty")
        self.assertEqual(response.status_code, 422)

    def test_unknown_specialty_on_experiment_cycle_is_a_clean_422(self) -> None:
        response = self.client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "not-a-real-specialty", "current_level": 1, "clone_id": "x"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_split_on_tasks_is_a_clean_422(self) -> None:
        response = self.client.get("/api/v1/tasks", params={"split": "not-a-real-split"})
        self.assertEqual(response.status_code, 422)

    def test_malformed_experiment_cycle_body_is_a_clean_422_not_a_500(self) -> None:
        response = self.client.post("/api/v1/experiment-cycles", json={"specialty": "research"})
        self.assertEqual(response.status_code, 422)


def _sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _a_pool(**overrides) -> ComputePool:
    defaults = {
        "pool_id": "local", "location": PoolLocation.LOCAL, "trust_level": TrustLevel.TRUSTED,
        "capabilities": (Capability.CPU_EVAL,),
    }
    defaults.update(overrides)
    return ComputePool(**defaults)


class TestNonHumanSubjectRefusedCleanly(unittest.TestCase):
    """La route `POST /experiment-cycles` force TOUJOURS `Subject.HUMAN` —
    aucun champ d'identité fourni par l'appelant HTTP n'est jamais transmis
    à `PolicyEngine` (voir docstring d'`apex.api.routes.evals`). Cette
    garantie est démontrée directement contre le mécanisme RÉEL dont la
    route dépend (`run_experiment_cycle` -> `TaskBankService.create` ->
    `PolicyEngine`), sans DB PostgreSQL (SQLite en mémoire suffit, même
    technique que `tests/test_metalearner_cycle.py`) : un sujet non-HUMAIN
    est refusé PROPREMENT (`PolicyDenied`), jamais un crash silencieux."""

    def setUp(self) -> None:
        self.factory = _sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)

    def _spawn_clone(self):
        return self.runtime.spawn(
            Subject.RESEARCH, pool=_a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )

    def test_a_clone_subject_is_denied_not_crashed(self) -> None:
        clone = self._spawn_clone()
        with self.assertRaises(PolicyDenied):
            run_experiment_cycle(
                session_factory=self.factory, agent_runtime=self.runtime, clone_id=clone.clone_id,
                specialty="research", current_level=1, requested_by=Subject.RESEARCH,
            )

    def test_core_is_also_denied_only_human_may_start_a_cycle(self) -> None:
        clone = self._spawn_clone()
        with self.assertRaises(PolicyDenied):
            run_experiment_cycle(
                session_factory=self.factory, agent_runtime=self.runtime, clone_id=clone.clone_id,
                specialty="research", current_level=1, requested_by=Subject.CORE,
            )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestEvalsRoutesWithRealDatabase(unittest.TestCase):
    """Base fraîche par test — même raison que les classes homologues de
    `tests/test_api.py` : plusieurs tests créent des Tasks/EvalRuns et
    vérifient des listes, un état partagé entre méthodes romprait
    l'hypothèse d'un test qui vérifie une liste vide."""

    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def _spawn_clone(self, runtime: AgentRuntime):
        return runtime.spawn(
            Subject.RESEARCH, pool=_a_pool(pool_id="local-gpu"), genome_ref="sha256:api-evals-test",
            spawned_by=Subject.CORE,
        )

    def test_tasks_list_and_get_are_real_and_empty_at_first(self) -> None:
        body = self.client.get("/api/v1/tasks").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"], [])

    def test_unknown_task_id_is_a_clean_404(self) -> None:
        response = self.client.get("/api/v1/tasks/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("introuvable", response.json()["detail"])

    def test_unknown_eval_run_id_is_a_clean_404(self) -> None:
        response = self.client.get("/api/v1/eval-runs/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("introuvable", response.json()["detail"])

    def test_weakness_with_no_data_yet_is_insufficient_data_not_a_500(self) -> None:
        body = self.client.get("/api/v1/weakness/research").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["category"], "insufficient_data")
        self.assertEqual(body["data"]["evidence_run_ids"], [])

    def test_self_model_with_no_data_yet_is_clean_not_a_500(self) -> None:
        body = self.client.get("/api/v1/self-model/research").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["specialty"], "research")
        self.assertEqual(body["data"]["acquired_skills"], [])
        self.assertEqual(body["data"]["weakness_report"]["category"], "insufficient_data")

    def test_experiment_cycle_end_to_end_produces_a_real_retrievable_eval_run(self) -> None:
        """La preuve centrale de cette passe : l'appel HTTP déclenche
        réellement `run_experiment_cycle` (sandbox réelle, jugement réel),
        jamais un cycle simulé — et l'`EvalRun` produit est ensuite
        retrouvable via `GET /eval-runs`/`GET /eval-runs/{id}`, exactement
        comme demandé par la mission."""
        factory = self._session_factory()
        runtime = AgentRuntime(factory)
        clone = self._spawn_clone(runtime)

        response = self.client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": clone.clone_id},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "real")
        eval_run = body["data"]["eval_run"]
        self.assertEqual(eval_run["oracle_verdict"], "pass")
        self.assertIsNone(eval_run["model_id"])  # chemin sandbox déterministe, jamais un ModelProvider
        self.assertEqual(eval_run["clone_id"], clone.clone_id)

        # Récupérable ensuite via GET /eval-runs et GET /eval-runs/{id} :
        listed = self.client.get("/api/v1/eval-runs").json()
        self.assertEqual(listed["status"], "real")
        self.assertIn(eval_run["id"], [r["id"] for r in listed["data"]])

        fetched = self.client.get(f"/api/v1/eval-runs/{eval_run['id']}").json()
        self.assertEqual(fetched["status"], "real")
        self.assertEqual(fetched["data"]["id"], eval_run["id"])

        # La Task générée par le cycle est elle aussi réellement persistée
        # et retrouvable :
        task = self.client.get(f"/api/v1/tasks/{body['data']['task']['id']}").json()
        self.assertEqual(task["status"], "real")

    def test_experiment_cycle_with_unreachable_clone_is_a_clean_422_not_a_500(self) -> None:
        response = self.client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": "does-not-exist"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("introuvable", response.json()["detail"])

    def test_weakness_reflects_a_real_eval_run_after_a_cycle(self) -> None:
        factory = self._session_factory()
        runtime = AgentRuntime(factory)
        clone = self._spawn_clone(runtime)
        self.client.post(
            "/api/v1/experiment-cycles",
            json={"specialty": "research", "current_level": 1, "clone_id": clone.clone_id},
        )
        body = self.client.get("/api/v1/weakness/research").json()
        self.assertEqual(body["data"]["category"], "no_weakness")
        self.assertEqual(body["data"]["pass_count"], 1)


if __name__ == "__main__":
    unittest.main()
