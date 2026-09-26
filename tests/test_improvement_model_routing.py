"""Le banc de la capacité « Learning / Model Routing » produit bien les
historiques qu'il annonce (vraies exécutions, vraies Experience) et observe
le choix du point d'entrée de production."""

from __future__ import annotations

import unittest

from apex.improvement.capabilities import model_routing as mr
from apex.metalearner.experience import ExperienceRepository, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory


def _experiences(env: mr._Env) -> list:
    from apex.db.engine import session_scope
    from apex.db.models import ExperienceRow

    with session_scope(env.factory) as session:
        ids = [row.id for row in session.query(ExperienceRow).all()]
    repo = ExperienceRepository(env.factory)
    return [repo.get(i) for i in ids]


class TestHistories(unittest.TestCase):
    def setUp(self) -> None:
        mr._Backend.behaviour = {}
        self.env = mr._Env()

    def test_a_wrong_model_produces_incorrect_solutions_attributed_to_it(self) -> None:
        self.env.history(mr.ALPHA, mr.WRONG, runs=1)
        experiences = _experiences(self.env)
        self.assertTrue(experiences)
        self.assertEqual({e.model_id for e in experiences}, {mr.ALPHA})
        self.assertEqual({e.failure_category for e in experiences}, {FailureCategory.INCORRECT_SOLUTION})

    def test_a_correct_model_produces_confirmed_successes(self) -> None:
        self.env.history(mr.BETA, mr.CORRECT, runs=1)
        self.assertEqual({e.success_evidence for e in _experiences(self.env)}, {SuccessEvidence.CONFIRMED})

    def test_a_lucky_model_produces_refuted_successes(self) -> None:
        self.env.history(mr.ALPHA, mr.LUCKY, runs=1)
        self.assertEqual({e.success_evidence for e in _experiences(self.env)}, {SuccessEvidence.REFUTED})

    def test_a_dead_model_produces_generation_failures(self) -> None:
        self.env.history(mr.ALPHA, mr.DEAD, runs=1)
        experiences = _experiences(self.env)
        self.assertEqual({e.failure_category for e in experiences}, {FailureCategory.GENERATION_FAILURE})

    def test_a_sandbox_outage_produces_infrastructure_failures_for_a_working_model(self) -> None:
        self.env.history(mr.ALPHA, mr.CORRECT, runs=1, sandbox_down=True)
        experiences = _experiences(self.env)
        self.assertEqual(
            {e.failure_category for e in experiences}, {FailureCategory.INFRASTRUCTURE_FAILURE}
        )
        self.assertEqual({e.model_id for e in experiences}, {mr.ALPHA})

    def test_the_decision_goes_through_the_production_entry_point(self) -> None:
        self.assertEqual(self.env.decide(), mr.ALPHA)


class TestAdapter(unittest.TestCase):
    def test_every_metric_has_a_scenario_and_the_fingerprint_is_stable(self) -> None:
        self.assertEqual({m.name for m in mr.SPEC.metrics}, set(mr.SCENARIOS))
        adapter = mr.ModelRoutingAdapter()
        self.assertEqual(adapter.benchmark_fingerprint(), adapter.benchmark_fingerprint())

    def test_the_judge_has_probes_for_every_cycle_kind(self) -> None:
        from apex.improvement.model import CycleKind

        judge = mr.build_judge()
        for kind in CycleKind:
            self.assertTrue(judge.probes_for(kind), kind)


if __name__ == "__main__":
    unittest.main()
