"""Tests d'`apex.metalearner.recommendation` — frontière Learning ->
Orchestrateur, préparation N+28 (mission N+26). SQLite en mémoire, même
patron que `tests/test_experience.py`."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.metalearner.experience import Experience, ExperienceRepository
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.recommendation import (
    Recommendation,
    RecommendationConfidence,
    recommend_from_experience,
)
from apex.oracles.model import OracleVerdict


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _an_experience(exp_id: str, task_id: str, category: FailureCategory, **overrides) -> Experience:
    defaults = dict(
        id=exp_id, task_id=task_id, eval_run_id=f"run-{exp_id}", candidate_id=f"run-{exp_id}",
        strategy_id="g0-mut0", generation_number=0, generation_config={},
        oracle_verdict=OracleVerdict.PASS if category is FailureCategory.SUCCESS else OracleVerdict.FAIL,
        fitness=1.0 if category is FailureCategory.SUCCESS else 0.0,
        failure_category=category, created_by="core", created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Experience(**defaults)


class TestRecommendFromExperienceRefusesInsufficientEvidence(unittest.TestCase):
    def test_returns_nothing_below_min_samples(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        repo.create(_an_experience("e1", "task-x", FailureCategory.SUCCESS))
        repo.create(_an_experience("e2", "task-x", FailureCategory.SUCCESS))
        # min_samples par défaut = 3, seulement 2 persistées.
        recs = recommend_from_experience("task-x", session_factory=factory)
        self.assertEqual(recs, ())

    def test_returns_nothing_for_an_unknown_task(self) -> None:
        factory = sqlite_session_factory()
        recs = recommend_from_experience("never-seen", session_factory=factory)
        self.assertEqual(recs, ())


class TestRecommendFromExperienceRealAggregation(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_high_success_rate_recommends_no_action(self) -> None:
        for i in range(8):
            self.repo.create(_an_experience(f"s{i}", "task-good", FailureCategory.SUCCESS))
        for i in range(2):
            self.repo.create(_an_experience(f"f{i}", "task-good", FailureCategory.EXECUTION_FAILURE))

        recs = recommend_from_experience("task-good", session_factory=self.factory)
        self.assertGreaterEqual(len(recs), 1)
        top = recs[0]
        self.assertIsInstance(top, Recommendation)
        self.assertIn("80%", top.analysis)
        self.assertIn("aucune action recommandée", top.recommendation)
        self.assertEqual(top.evidence_count, 10)
        self.assertEqual(len(top.evidence_refs), 10)

    def test_low_success_rate_recommends_a_different_strategy(self) -> None:
        for i in range(2):
            self.repo.create(_an_experience(f"s{i}", "task-bad", FailureCategory.SUCCESS))
        for i in range(6):
            self.repo.create(_an_experience(f"f{i}", "task-bad", FailureCategory.INCORRECT_SOLUTION))

        recs = recommend_from_experience("task-bad", session_factory=self.factory)
        top = recs[0]
        self.assertIn("stratégie de génération différente", top.recommendation)

    def test_dominant_failure_category_produces_a_second_recommendation(self) -> None:
        for i in range(3):
            self.repo.create(_an_experience(f"s{i}", "task-mixed", FailureCategory.SUCCESS))
        for i in range(5):
            self.repo.create(_an_experience(f"t{i}", "task-mixed", FailureCategory.TIMEOUT))
        for i in range(1):
            self.repo.create(_an_experience(f"i{i}", "task-mixed", FailureCategory.INVALID_CANDIDATE))

        recs = recommend_from_experience("task-mixed", session_factory=self.factory)
        self.assertEqual(len(recs), 2)
        category_rec = recs[1]
        self.assertIn("timeout", category_rec.observation)
        self.assertEqual(category_rec.evidence_count, 5)

    def test_confidence_reflects_sample_size_only(self) -> None:
        for i in range(3):
            self.repo.create(_an_experience(f"s{i}", "task-small", FailureCategory.SUCCESS))
        recs = recommend_from_experience("task-small", session_factory=self.factory)
        self.assertIs(recs[0].confidence, RecommendationConfidence.LOW)

        for i in range(10):
            self.repo.create(_an_experience(f"s2-{i}", "task-big", FailureCategory.SUCCESS))
        recs_big = recommend_from_experience("task-big", session_factory=self.factory)
        self.assertIs(recs_big[0].confidence, RecommendationConfidence.HIGH)

    def test_recommendation_round_trips_to_dict(self) -> None:
        for i in range(3):
            self.repo.create(_an_experience(f"s{i}", "task-dict", FailureCategory.SUCCESS))
        recs = recommend_from_experience("task-dict", session_factory=self.factory)
        as_dict = recs[0].to_dict()
        self.assertIn("confidence", as_dict)
        self.assertIsInstance(as_dict["evidence_refs"], list)


class TestNeverDecides(unittest.TestCase):
    def test_recommendation_source_protocol_has_no_decide_method(self) -> None:
        """Garantie STRUCTURELLE, pas seulement documentée : le contrat
        n'expose aucune méthode `decide`/`apply`/`enforce`."""
        from apex.metalearner.recommendation import RecommendationSource

        forbidden = {"decide", "apply", "enforce", "execute", "mutate_policy"}
        declared = set(RecommendationSource.__protocol_attrs__) if hasattr(
            RecommendationSource, "__protocol_attrs__"
        ) else {
            name for name in dir(RecommendationSource)
            if not name.startswith("_")
        }
        self.assertEqual(declared & forbidden, set())

    def test_module_never_imports_recovery_policy(self) -> None:
        """`apex.recovery.decide`/`apex.config.schema.RecoveryPolicy` ne
        sont jamais IMPORTÉS par ce module — vérifié sur les lignes
        `import`/`from` réelles du code (jamais la docstring, qui EN PARLE
        délibérément pour documenter la frontière)."""
        import inspect

        import apex.metalearner.recommendation as mod

        import_lines = [
            line.strip() for line in inspect.getsource(mod).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            self.assertNotIn("apex.recovery", line)
            self.assertNotIn("RecoveryPolicy", line)


if __name__ == "__main__":
    unittest.main()
