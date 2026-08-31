"""Tests de CostGainEstimator — aucune DB, aucun réseau.

Vérifie : coût réel additionné honnêtement (jamais un sous-comptage
silencieux), UNKNOWN jamais transformé en 0, composition réelle de StatTest
pour le gain, ratio calculé UNIQUEMENT sur un gain significatif et un coût
réellement mesuré, jamais une fausse précision.
"""

from __future__ import annotations

import unittest

from apex.costgain.estimator import CostGainEstimator
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.obs.status import DataStatus
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus
from apex.stattest.model import StatTestVerdict


def a_run(elapsed_s: float | None, *, memory_peak_mb: float | None = None) -> EvalRun:
    facts = {"memory_peak_mb": memory_peak_mb} if memory_peak_mb is not None else {}
    return EvalRun.new(
        task_id="t", clone_id="c", method=EvalMethod.PRACTICE, oracle_verdict=OracleVerdict.PASS,
        oracle_detail=None, sandbox_status=ExecutionStatus.SUCCESS, exit_code=0,
        elapsed_s=elapsed_s, facts=facts, created_by="core",
    )


class TestEstimateCost(unittest.TestCase):
    def setUp(self) -> None:
        self.estimator = CostGainEstimator()

    def test_no_runs_is_unknown_elapsed_and_zero_count(self) -> None:
        cost = self.estimator.estimate_cost([])
        self.assertEqual(cost.elapsed_s.status, DataStatus.UNKNOWN)
        self.assertEqual(cost.experiment_count, 0)

    def test_all_measured_sums_to_a_real_total(self) -> None:
        cost = self.estimator.estimate_cost([a_run(1.0), a_run(2.0), a_run(3.0)])
        self.assertEqual(cost.elapsed_s.status, DataStatus.REAL)
        self.assertEqual(cost.elapsed_s.value, 6.0)
        self.assertEqual(cost.experiment_count, 3)

    def test_a_single_missing_value_makes_the_total_unknown_not_undercounted(self) -> None:
        """Additionner seulement les valeurs connues sous-compterait
        silencieusement — le total doit rester UNKNOWN plutôt qu'un chiffre
        trompeur."""
        cost = self.estimator.estimate_cost([a_run(1.0), a_run(None), a_run(3.0)])
        self.assertEqual(cost.elapsed_s.status, DataStatus.UNKNOWN)
        self.assertIsNone(cost.elapsed_s.value)
        self.assertEqual(cost.experiment_count, 3)

    def test_memory_peak_is_the_real_maximum_across_runs_not_a_sum(self) -> None:
        """Un pic n'est pas additif entre runs séparés — le pic RÉEL le plus
        élevé observé, jamais une somme (qui gonflerait artificiellement le
        coût mémoire d'une stratégie exécutée sur plusieurs runs)."""
        cost = self.estimator.estimate_cost(
            [a_run(1.0, memory_peak_mb=10.0), a_run(1.0, memory_peak_mb=25.0), a_run(1.0, memory_peak_mb=15.0)]
        )
        self.assertEqual(cost.memory_peak_mb.status, DataStatus.REAL)
        self.assertEqual(cost.memory_peak_mb.value, 25.0)

    def test_memory_peak_is_unknown_when_any_run_lacks_it(self) -> None:
        cost = self.estimator.estimate_cost([a_run(1.0, memory_peak_mb=10.0), a_run(1.0)])
        self.assertEqual(cost.memory_peak_mb.status, DataStatus.UNKNOWN)

    def test_cpu_time_stays_unknown_even_with_full_memory_and_elapsed_data(self) -> None:
        """`cpu_time_s` n'est jamais dérivé d'une autre mesure — limite
        réelle du backend cgroups v1, jamais comblée par une approximation."""
        cost = self.estimator.estimate_cost([a_run(1.0, memory_peak_mb=10.0)])
        self.assertEqual(cost.cpu_time_s.status, DataStatus.UNKNOWN)

    def test_cpu_time_memory_and_tokens_are_always_honestly_unknown_today(self) -> None:
        """Ni fabriqués, ni omis silencieusement : le champ existe, marqué
        UNKNOWN, jamais 0."""
        cost = self.estimator.estimate_cost([a_run(1.0)])
        self.assertEqual(cost.cpu_time_s.status, DataStatus.UNKNOWN)
        self.assertIsNone(cost.cpu_time_s.value)
        self.assertEqual(cost.memory_peak_mb.status, DataStatus.UNKNOWN)
        self.assertEqual(cost.token_count.status, DataStatus.UNKNOWN)


class TestEstimateGainDelegatesToStatTest(unittest.TestCase):
    def test_clear_improvement_is_significant(self) -> None:
        estimator = CostGainEstimator()
        result = estimator.estimate_gain([0.0] * 10, [1.0] * 10)
        self.assertEqual(result.verdict, StatTestVerdict.SIGNIFICANT)

    def test_no_data_is_insufficient(self) -> None:
        estimator = CostGainEstimator()
        result = estimator.estimate_gain([], [])
        self.assertEqual(result.verdict, StatTestVerdict.INSUFFICIENT_DATA)

    def test_identical_before_after_is_not_significant(self) -> None:
        estimator = CostGainEstimator()
        seq = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        result = estimator.estimate_gain(seq, list(seq))
        self.assertEqual(result.verdict, StatTestVerdict.NOT_SIGNIFICANT)


class TestRatioNeverFabricated(unittest.TestCase):
    def setUp(self) -> None:
        self.estimator = CostGainEstimator()
        self.runs = [a_run(1.0), a_run(2.0), a_run(1.5)]

    def test_significant_gain_and_real_cost_yields_a_real_ratio(self) -> None:
        result = self.estimator.estimate(
            specialty="research", eval_runs=self.runs, before=[0.0] * 8, after=[1.0] * 8,
        )
        self.assertEqual(result.ratio.status, DataStatus.REAL)
        self.assertAlmostEqual(result.ratio.value, result.gain.observed_diff / 4.5)

    def test_not_significant_gain_yields_unknown_ratio_never_zero(self) -> None:
        seq = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        result = self.estimator.estimate(
            specialty="research", eval_runs=self.runs, before=seq, after=list(seq),
        )
        self.assertEqual(result.gain.verdict, StatTestVerdict.NOT_SIGNIFICANT)
        self.assertEqual(result.ratio.status, DataStatus.UNKNOWN)
        self.assertIsNone(result.ratio.value)

    def test_insufficient_gain_data_yields_unknown_ratio(self) -> None:
        result = self.estimator.estimate(
            specialty="research", eval_runs=self.runs, before=[], after=[],
        )
        self.assertEqual(result.gain.verdict, StatTestVerdict.INSUFFICIENT_DATA)
        self.assertEqual(result.ratio.status, DataStatus.UNKNOWN)

    def test_significant_gain_but_unmeasured_cost_yields_unknown_ratio(self) -> None:
        runs_with_gap = [a_run(1.0), a_run(None)]
        result = self.estimator.estimate(
            specialty="research", eval_runs=runs_with_gap, before=[0.0] * 8, after=[1.0] * 8,
        )
        self.assertEqual(result.gain.verdict, StatTestVerdict.SIGNIFICANT)
        self.assertEqual(result.ratio.status, DataStatus.UNKNOWN)

    def test_reason_is_always_populated_and_explains_unknown_ratios(self) -> None:
        result = self.estimator.estimate(
            specialty="research", eval_runs=self.runs, before=[], after=[],
        )
        self.assertIn("UNKNOWN", result.reason)


class TestSpecialtyIsCarriedThrough(unittest.TestCase):
    def test_estimate_carries_the_specialty_verbatim(self) -> None:
        estimator = CostGainEstimator()
        result = estimator.estimate(
            specialty="research", eval_runs=[a_run(1.0)] * 5, before=[0.0] * 5, after=[1.0] * 5,
        )
        self.assertEqual(result.specialty, "research")


if __name__ == "__main__":
    unittest.main()
