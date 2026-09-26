"""Experience jugée -> signal de performance -> choix du modèle par `apex solve`."""

from __future__ import annotations

import dataclasses
import itertools
import unittest

from sqlalchemy import create_engine

from apex.config.loader import load_config
from apex.config.schema import ModelEndpoint, ModelRef, ModelRole
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.metalearner.experience import Experience, ExperienceRepository, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.model_performance import performance_from_experiences
from apex.metalearner.solution_loop_runner import choose_endpoint, resolve_local_pool
from apex.models.registry import filter_by_role, resolve_candidates
from apex.oracles.model import OracleVerdict

ALPHA, BETA = "alpha-coder:1", "beta-coder:1"
_ids = itertools.count()


def config():
    base = load_config()
    pool = resolve_local_pool(base)
    refs = tuple(ModelRef(n.split(":")[0], "1", "q4", "coder") for n in (ALPHA, BETA))
    endpoints = tuple(
        ModelEndpoint(
            pool_id=pool.pool_id,
            runtime="ollama",
            url="http://127.0.0.1:11434",
            served_model=n,
            max_context=8192,
        )
        for n in (ALPHA, BETA)
    )
    roles = dict(base.model_roles)
    roles["coder_main"] = ModelRole(role="coder_main", candidates=refs, selection="benchmark_driven")
    return dataclasses.replace(base, model_roles=roles, endpoints=base.endpoints + endpoints)


def experience(
    model: str | None, category: FailureCategory, evidence: SuccessEvidence | None = None
) -> Experience:
    n = next(_ids)
    return Experience(
        id=f"e{n}",
        task_id="t",
        eval_run_id=f"r{n}",
        candidate_id=f"r{n}",
        strategy_id="s",
        generation_number=0,
        generation_config={},
        oracle_verdict=OracleVerdict.PASS if category is FailureCategory.SUCCESS else OracleVerdict.FAIL,
        fitness=1.0,
        failure_category=category,
        created_by="core",
        model_id=model,
        success_evidence=evidence,
    )


SUCCESS, INCORRECT = FailureCategory.SUCCESS, FailureCategory.INCORRECT_SOLUTION


class TestPerformanceFromExperiences(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = filter_by_role(resolve_candidates(config()), "coder_main")
        self.alpha = next(
            c.ref.id for c in self.candidates if c.endpoint and c.endpoint.served_model == ALPHA
        )

    def stats(self, *experiences: Experience):
        return performance_from_experiences(experiences, self.candidates).get(self.alpha)

    def test_only_confirmed_successes_count_as_successes(self) -> None:
        stats = self.stats(
            experience(ALPHA, SUCCESS, SuccessEvidence.CONFIRMED),
            experience(ALPHA, SUCCESS, SuccessEvidence.OBSERVED),
            experience(ALPHA, SUCCESS, SuccessEvidence.REFUTED),
        )
        self.assertEqual((stats.usage_count, stats.success_count, stats.failure_count), (2, 1, 1))

    def test_infrastructure_failures_are_not_attributed_to_the_model(self) -> None:
        stats = self.stats(
            experience(ALPHA, SUCCESS, SuccessEvidence.CONFIRMED),
            experience(ALPHA, FailureCategory.INFRASTRUCTURE_FAILURE),
            experience(ALPHA, FailureCategory.TEST_ORACLE_FAILURE),
        )
        self.assertEqual((stats.usage_count, stats.success_rate.value), (1, 1.0))

    def test_code_failures_count_against_the_model(self) -> None:
        stats = self.stats(experience(ALPHA, INCORRECT), experience(ALPHA, FailureCategory.TIMEOUT))
        self.assertEqual((stats.failure_count, stats.success_rate.value), (2, 0.0))
        self.assertEqual(stats.failure_reasons, {"incorrect_solution": 1, "timeout": 1})

    def test_replays_and_foreign_or_prefix_matching_names_are_ignored(self) -> None:
        stats = self.stats(
            experience(f"replay:{ALPHA}", SUCCESS, SuccessEvidence.CONFIRMED),
            experience("alpha-coder:10", INCORRECT),
            experience(None, FailureCategory.GENERATION_FAILURE),
        )
        self.assertIsNone(stats)


class TestChooseEndpoint(unittest.TestCase):
    """Test causal : même configuration, historiques différents -> choix différents."""

    def choose(self, *experiences: Experience) -> str:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        repo = ExperienceRepository(build_session_factory(engine))
        for e in experiences:
            repo.create(e)
        endpoint, _ = choose_endpoint(config(), "coder_main", repo)
        return endpoint.served_model

    def test_without_history_the_reference_is_chosen(self) -> None:
        self.assertEqual(self.choose(), ALPHA)

    def test_history_a_keeps_the_reference_history_b_switches(self) -> None:
        history_a = (
            experience(ALPHA, SUCCESS, SuccessEvidence.CONFIRMED),
            experience(BETA, INCORRECT),
        )
        history_b = (
            experience(ALPHA, INCORRECT),
            experience(BETA, SUCCESS, SuccessEvidence.CONFIRMED),
        )
        self.assertEqual(self.choose(*history_a), ALPHA)
        self.assertEqual(self.choose(*history_b), BETA)

    def test_lucky_successes_do_not_make_a_model_preferred(self) -> None:
        chosen = self.choose(
            experience(ALPHA, SUCCESS, SuccessEvidence.REFUTED),
            experience(BETA, SUCCESS, SuccessEvidence.CONFIRMED),
        )
        self.assertEqual(chosen, BETA)


if __name__ == "__main__":
    unittest.main()


class TestDeadModelAttribution(unittest.TestCase):
    def test_a_generation_failure_counts_against_the_requested_model_only_when_known(self) -> None:
        candidates = filter_by_role(resolve_candidates(config()), "coder_main")
        alpha = next(c.ref.id for c in candidates if c.endpoint and c.endpoint.served_model == ALPHA)
        failure = experience(None, FailureCategory.GENERATION_FAILURE)
        self.assertEqual(performance_from_experiences([failure], candidates), {})
        stats = performance_from_experiences([failure], candidates, requested_models={failure.id: ALPHA})
        self.assertEqual(
            (stats[alpha].failure_count, stats[alpha].failure_reasons), (1, {"generation_failure": 1})
        )

    def test_the_evalrunner_records_the_requested_model_when_generation_fails(self) -> None:
        import apex.improvement.capabilities.model_routing as mr

        mr._Backend.behaviour = {}
        env = mr._Env()
        env.history(ALPHA, mr.DEAD, runs=1)
        from apex.db.engine import session_scope
        from apex.db.models import EvalRunRow

        with session_scope(env.factory) as session:
            facts = [row.facts for row in session.query(EvalRunRow).all()]
        self.assertTrue(facts)
        self.assertEqual({f.get("requested_model") for f in facts}, {ALPHA})

    def test_a_dead_reference_is_abandoned_for_an_untried_model_through_apex_solve(self) -> None:
        import apex.improvement.capabilities.model_routing as mr

        mr._Backend.behaviour = {}
        env = mr._Env()
        env.history(ALPHA, mr.DEAD)
        self.assertEqual(env.decide(), BETA)
