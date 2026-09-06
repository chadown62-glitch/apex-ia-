"""Tests de `apex.metalearner.population_fitness.compute_fitness` — fonction PURE,
déterministe, sur des métriques RÉELLEMENT mesurées d'un `EvalRun`, jamais
inventées (voir sa docstring)."""

from __future__ import annotations

import unittest

from apex.evalrunner.model import EvalMethod, EvalRun
from apex.oracles.model import OracleVerdict
from apex.metalearner.population_fitness import compute_fitness
from apex.sandbox.model import ResourceLimits


def _limits(timeout_s: float = 60.0) -> ResourceLimits:
    return ResourceLimits(cpu_cores=2.0, memory_mb=512, pids_limit=32, timeout_s=timeout_s, disk_mb=64, network="none")


def _run(*, verdict: OracleVerdict, elapsed_s: float | None) -> EvalRun:
    return EvalRun.new(
        task_id="task-1", method=EvalMethod.PRACTICE, oracle_verdict=verdict, oracle_detail=None,
        exit_code=0 if verdict is OracleVerdict.PASS else 1, elapsed_s=elapsed_s, facts={}, created_by="core",
    )


class TestDeterminism(unittest.TestCase):
    def test_same_run_and_limits_always_yield_the_same_fitness(self) -> None:
        run = _run(verdict=OracleVerdict.PASS, elapsed_s=3.0)
        limits = _limits()
        first = compute_fitness(run, limits=limits)
        second = compute_fitness(run, limits=limits)
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.components, second.components)


class TestQuality(unittest.TestCase):
    def test_pass_scores_full_quality(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=1.0), limits=_limits())
        self.assertEqual(result.components["quality"], 1.0)

    def test_fail_scores_zero_quality_but_stays_robust(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.FAIL, elapsed_s=1.0), limits=_limits())
        self.assertEqual(result.components["quality"], 0.0)
        self.assertEqual(result.components["robustness"], 1.0)  # le sandbox a bien tourné

    def test_pass_outscores_fail_all_else_equal(self) -> None:
        passing = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=1.0), limits=_limits())
        failing = compute_fitness(_run(verdict=OracleVerdict.FAIL, elapsed_s=1.0), limits=_limits())
        self.assertGreater(passing.value, failing.value)


class TestErrorPenalty(unittest.TestCase):
    def test_error_is_penalized_and_not_robust(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.ERROR, elapsed_s=None), limits=_limits())
        self.assertEqual(result.components["error_penalty"], 1.0)
        self.assertEqual(result.components["robustness"], 0.0)

    def test_timeout_is_penalized_and_not_robust(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.TIMEOUT, elapsed_s=None), limits=_limits())
        self.assertEqual(result.components["error_penalty"], 1.0)

    def test_error_scores_lower_than_a_plain_fail(self) -> None:
        failing = compute_fitness(_run(verdict=OracleVerdict.FAIL, elapsed_s=1.0), limits=_limits())
        erroring = compute_fitness(_run(verdict=OracleVerdict.ERROR, elapsed_s=None), limits=_limits())
        self.assertGreater(failing.value, erroring.value)


class TestCostPenalty(unittest.TestCase):
    def test_unknown_elapsed_time_never_fabricates_a_cost(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=None), limits=_limits())
        self.assertFalse(result.components["cost_known"])
        self.assertEqual(result.components["cost_penalty"], 0.0)

    def test_known_elapsed_time_is_normalized_by_the_real_timeout_budget(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=30.0), limits=_limits(timeout_s=60.0))
        self.assertTrue(result.components["cost_known"])
        self.assertAlmostEqual(result.components["cost_penalty"], 0.5)

    def test_slower_execution_scores_lower_all_else_equal(self) -> None:
        fast = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=1.0), limits=_limits())
        slow = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=50.0), limits=_limits())
        self.assertGreater(fast.value, slow.value)

    def test_cost_penalty_never_exceeds_one_even_past_the_budget(self) -> None:
        result = compute_fitness(_run(verdict=OracleVerdict.PASS, elapsed_s=500.0), limits=_limits(timeout_s=60.0))
        self.assertLessEqual(result.components["cost_penalty"], 1.0)


if __name__ == "__main__":
    unittest.main()
