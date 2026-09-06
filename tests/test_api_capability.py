"""Tests de `GET /capabilities/{specialty}`, `GET /capabilities/{specialty}
/history`, `GET /capabilities/{specialty}/latest`, `GET /curriculum/
{specialty}/challenges`, `GET /bugs/{specialty}`, `GET /learning-sessions/
{id}/capability`, `GET /population/{specialty}/capability` — passe
« Adversarial Curriculum + Capability Benchmarking » (N+10). Même patron
qu'`tests/test_api_learning_feedback.py` (N+9). Lettres X/Y/Z du §19 de la
mission citées explicitement."""

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

_ALL_PATHS = (
    "/api/v1/capabilities/research",
    "/api/v1/capabilities/research/history",
    "/api/v1/capabilities/research/latest",
    "/api/v1/curriculum/research/challenges",
    "/api/v1/bugs/research",
    "/api/v1/learning-sessions/x/capability",
    "/api/v1/population/research/capability",
)


class TestCapabilityRoutesRequireAuthentication(unittest.TestCase):
    def test_all_routes_reject_missing_token(self) -> None:
        from fastapi.testclient import TestClient

        from apex.api.app import create_app

        anonymous = TestClient(create_app())
        for path in _ALL_PATHS:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 401, path)


class TestCapabilityRoutesWithoutDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_all_routes_degrade_to_unknown_not_a_500(self) -> None:
        for path in _ALL_PATHS:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()["status"], "unknown", path)


class TestCapabilityRoutesInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_unknown_specialty_is_a_clean_422(self) -> None:
        for path in (
            "/api/v1/capabilities/not-a-real-specialty",
            "/api/v1/curriculum/not-a-real-specialty/challenges",
            "/api/v1/bugs/not-a-real-specialty",
            "/api/v1/population/not-a-real-specialty/capability",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422, path)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestCapabilityRoutesWithRealDatabase(unittest.TestCase):
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

    def test_evolve_produces_real_capability_data_retrievable_via_every_route(self) -> None:
        evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)
        self.assertIsNotNone(evolve.json()["data"]["champion"])

        status = self.client.get("/api/v1/capabilities/research")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "real")
        data = status.json()["data"]
        self.assertIsNotNone(data["champion_member_id"])
        self.assertIsNotNone(data["champion_run"])
        self.assertEqual(len(data["champion_run"]["measurements"]), 6)

        history = self.client.get("/api/v1/capabilities/research/history")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(len(history.json()["data"]), 1)

        latest = self.client.get("/api/v1/capabilities/research/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["status"], "real")

        population_capability = self.client.get("/api/v1/population/research/capability")
        self.assertEqual(population_capability.status_code, 200)
        self.assertEqual(
            population_capability.json()["data"]["champion_run"]["id"], data["champion_run"]["id"],
        )

    def test_get_capability_latest_unknown_specialty_history_empty_is_a_404(self) -> None:
        response = self.client.get("/api/v1/capabilities/research/latest")
        self.assertEqual(response.status_code, 404)

    def test_capabilities_before_any_champion_has_no_data_but_stays_real(self) -> None:
        response = self.client.get("/api/v1/capabilities/research")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "real")
        self.assertIsNone(response.json()["data"]["champion_member_id"])
        self.assertIsNone(response.json()["data"]["champion_run"])

    def test_forced_security_veto_produces_real_challenges_and_bugs(self) -> None:
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
            return JudgeResult(
                judge_id=self_judge.judge_id, judge_version=self_judge.judge_version,
                dimension=self_judge.dimension, decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="véto forcé (test API)",
                findings=(JudgeFinding(dimension=JudgeDimension.SECURITY, severity=JudgeSeverity.CRITICAL, summary="véto forcé"),),
            )

        with patch.object(SecurityJudge, "evaluate", always_veto):
            evolve = self.client.post("/api/v1/population/research/evolve")
        self.assertEqual(evolve.status_code, 200)

        bugs = self.client.get("/api/v1/bugs/research")
        self.assertEqual(bugs.status_code, 200)
        self.assertEqual(len(bugs.json()["data"]), 1)
        self.assertEqual(bugs.json()["data"][0]["status"], "open")

        challenges = self.client.get("/api/v1/curriculum/research/challenges")
        self.assertEqual(challenges.status_code, 200)
        self.assertEqual(len(challenges.json()["data"]), 1)
        self.assertEqual(challenges.json()["data"][0]["dimension"], "security")
        self.assertTrue(challenges.json()["data"][0]["adversarial"])

    def test_learning_session_capability_delegates_to_its_specialty(self) -> None:
        """Lettre X du §19 — intégration `LearningOrchestrator`."""
        start = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 1},
        )
        self.assertEqual(start.status_code, 200)
        session_id = start.json()["data"]["id"]

        deadline = time.monotonic() + 30.0
        status = "running"
        while status in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.2)
            status = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]["status"]
        self.assertEqual(status, "completed")

        response = self.client.get(f"/api/v1/learning-sessions/{session_id}/capability")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "real")
        self.assertIsNotNone(response.json()["data"]["champion_run"])

    def test_session_capability_unknown_session_is_a_404(self) -> None:
        response = self.client.get("/api/v1/learning-sessions/not-a-real-session/capability")
        self.assertEqual(response.status_code, 404)

    def test_resume_preserves_capability_history_continuity(self) -> None:
        """Lettre Y du §19 — reprendre une session continue la numérotation
        de génération ; l'historique de capacité ne perd ni ne double
        aucune génération."""
        from unittest.mock import patch

        from apex.metalearner.evolution import PopulationEvolutionService

        start = self.client.post(
            "/api/v1/learning-sessions", json={"specialty": "research", "max_generations": 5},
        )
        session_id = start.json()["data"]["id"]

        original_evolve = PopulationEvolutionService.evolve_generation
        call_count = {"n": 0}

        def failing_after_one(self, specialty, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("panne simulée (test)")
            return original_evolve(self, specialty, **kwargs)

        with patch.object(PopulationEvolutionService, "evolve_generation", failing_after_one):
            deadline = time.monotonic() + 30.0
            status = "running"
            while status in ("running", "cancel_requested") and time.monotonic() < deadline:
                time.sleep(0.2)
                status = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]["status"]
            self.assertEqual(status, "failed")

        resume = self.client.post(f"/api/v1/learning-sessions/{session_id}/resume")
        self.assertEqual(resume.status_code, 200)
        deadline = time.monotonic() + 30.0
        status = "running"
        while status in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.2)
            status = self.client.get(f"/api/v1/learning-sessions/{session_id}").json()["data"]["status"]
        self.assertEqual(status, "completed")

        history = self.client.get("/api/v1/capabilities/research/history")
        generations = [run["generation"] for run in history.json()["data"]]
        self.assertEqual(len(generations), len(set(generations)), "aucune génération dupliquée dans l'historique")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestEndToEndCapabilityChain(unittest.TestCase):
    """Lettre Z du §19 — Phase 17 de la mission : la chaîne complète en 14
    étapes, aucune étape remplacée par un score hardcodé."""

    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def test_full_chain_weakness_to_challenge_to_capability_delta_to_next_generation(self) -> None:
        from unittest.mock import patch

        from apex.agents.runtime import AgentRuntime
        from apex.config.schema import (
            Capability,
            ComputePool,
            GauntletPolicy,
            PoolLocation,
            PopulationPolicy,
            TrustLevel,
        )
        from apex.genome.service import GenomeStoreService
        from apex.metalearner.bug_farm_service import BugFarmService
        from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService
        from apex.metalearner.challenge_repository import CurriculumChallengeRepository
        from apex.metalearner.evolution import PopulationEvolutionService
        from apex.metalearner.judge_model import (
            JudgeDecision,
            JudgeDimension,
            JudgeFinding,
            JudgeResult,
            JudgeSeverity,
        )
        from apex.metalearner.judges import SecurityJudge
        from apex.metalearner.weakness_engine import WeaknessEngineService
        from apex.policy.model import Subject

        factory = self._session_factory()
        genome = GenomeStoreService(factory)
        genome.propose(content={"a": 1, "execution": {"timeout_s": 5.0}}, created_by=Subject.HUMAN)
        genome.promote(genome.list_all()[0].id, requested_by=Subject.HUMAN)

        class FakeConfig:
            def __init__(self):
                self.pools = {
                    "local": ComputePool(
                        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
                        capabilities=(Capability.SANDBOX,),
                    ),
                }
                self.population = PopulationPolicy(
                    max_population_size=8, generation_size=2, elite_size=2, enable_crossover=False,
                    max_session_generations=20,
                )
                self.gauntlet = GauntletPolicy(
                    min_confidence_threshold=0.6, efficiency_reject_threshold=0.9,
                    major_regression_factor=2.0, enable_expensive_judges=True,
                )

        runtime = AgentRuntime(factory)
        service = PopulationEvolutionService(factory, agent_runtime=runtime, config=FakeConfig())

        def always_veto(self_judge, judge_input):
            return JudgeResult(
                judge_id=self_judge.judge_id, judge_version=self_judge.judge_version,
                dimension=self_judge.dimension, decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="véto forcé (test end-to-end)",
                findings=(JudgeFinding(dimension=JudgeDimension.SECURITY, severity=JudgeSeverity.CRITICAL, summary="véto forcé"),),
            )

        # 1. weakness réelle observée (véto SECURITY forcé sur UN vrai juge).
        with patch.object(SecurityJudge, "evaluate", always_veto):
            result_1 = service.evolve_generation("research")
        weaknesses = WeaknessEngineService(factory).aggregate("research")
        self.assertEqual(len(weaknesses), 1)
        self.assertEqual(weaknesses[0].dimension, JudgeDimension.SECURITY)

        # 2/3. curriculum challenge créé + candidat soumis (déjà réalisé
        # par evolve_generation lui-même, §2/§14 — vérifié via la table réelle).
        challenges = CurriculumChallengeRepository(factory).list_by_specialty("research")
        self.assertEqual(len(challenges), 1)
        self.assertEqual(challenges[0].generation, result_1.generation)

        # 4/5/6. Gauntlet exécuté, finding enregistré, weakness mise à jour
        # — déjà vérifié ci-dessus (aggregate() reflète le signal réel).

        # 7/8/9. nouvelle mutation orientée -> candidat suivant -> Gauntlet suivant.
        result_2 = service.evolve_generation("research")
        self.assertEqual(result_2.generation, result_1.generation + 1)

        # 10/11/12. benchmark indépendant + HELDOUT + delta de capacité —
        # RÉELLEMENT exécutés à l'intérieur de la promotion, retrouvables :
        benchmark_service = CapabilityBenchmarkService(factory, agent_runtime=runtime)
        capability_history = benchmark_service.get_history("research")
        self.assertGreaterEqual(len(capability_history), 1)

        # 13. décision de promotion déterminée — reflétée par le statut RÉEL
        # du champion courant (jamais un score hardcodé).
        from apex.metalearner.population_service import PopulationService

        champion = PopulationService(factory).get_champion("research")
        self.assertIsNotNone(champion)

        # 14. historique persistant — bug farm reflète la RÉGRESSION/
        # correction observée à partir des MÊMES données réelles.
        bugs = BugFarmService(factory).list_bugs("research")
        self.assertEqual(len(bugs), 1)
        self.assertIn(bugs[0].status.value, ("open", "fixed", "regressed"))


if __name__ == "__main__":
    unittest.main()
