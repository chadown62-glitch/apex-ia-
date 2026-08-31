"""Tests de WeaknessDetector — classification pure, aucune DB.

Vérifie les exigences explicites de la directive Phase 12 : données
insuffisantes, échec isolé, échecs répétés, faiblesse statistiquement
crédible, absence de faiblesse, exclusion des ERROR/TIMEOUT/UNKNOWN du
calcul, traçabilité (evidence_run_ids), et reproductibilité (déterminisme).
"""

from __future__ import annotations

import unittest

from apex.evalrunner.model import EvalMethod, EvalRun
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus
from apex.weakness.detector import (
    CREDIBLE_FAILURE_RATE_THRESHOLD,
    MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS,
    WeaknessDetector,
)
from apex.weakness.model import WeaknessCategory


def _with_id(verdict: OracleVerdict, run_id: str) -> EvalRun:
    run = EvalRun.new(
        task_id="t1", clone_id="c1", method=EvalMethod.PRACTICE, oracle_verdict=verdict,
        oracle_detail=None, sandbox_status=ExecutionStatus.SUCCESS, exit_code=0,
        elapsed_s=0.1, facts={}, created_by="core",
    )
    return EvalRun(
        id=run_id, task_id=run.task_id, clone_id=run.clone_id, method=run.method,
        oracle_verdict=run.oracle_verdict, oracle_detail=run.oracle_detail,
        sandbox_status=run.sandbox_status, exit_code=run.exit_code, elapsed_s=run.elapsed_s,
        facts=run.facts, created_by=run.created_by,
    )


class TestInsufficientData(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = WeaknessDetector()

    def test_no_runs_at_all_is_insufficient_data(self) -> None:
        report = self.detector.detect([])
        self.assertEqual(report.category, WeaknessCategory.INSUFFICIENT_DATA)
        self.assertIsNone(report.failure_rate)
        self.assertEqual(report.evidence_run_ids, ())

    def test_only_error_timeout_unknown_runs_is_insufficient_data(self) -> None:
        """Un run ERROR/TIMEOUT/UNKNOWN n'est jamais confondu avec un signal
        PASS/FAIL — même un grand nombre d'entre eux reste « pas de données »."""
        runs = [
            _with_id(OracleVerdict.ERROR, "r1"),
            _with_id(OracleVerdict.TIMEOUT, "r2"),
            _with_id(OracleVerdict.UNKNOWN, "r3"),
        ]
        report = self.detector.detect(runs)
        self.assertEqual(report.category, WeaknessCategory.INSUFFICIENT_DATA)
        self.assertEqual(report.error_count, 1)
        self.assertEqual(report.timeout_count, 1)
        self.assertEqual(report.unknown_count, 1)
        self.assertIsNone(report.failure_rate)


class TestNoWeakness(unittest.TestCase):
    def test_all_passes_is_no_weakness(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, f"r{i}") for i in range(3)]
        report = detector.detect(runs)
        self.assertEqual(report.category, WeaknessCategory.NO_WEAKNESS)
        self.assertEqual(report.failure_rate, 0.0)


class TestIsolatedFailure(unittest.TestCase):
    def test_single_failure_among_few_passes_is_isolated(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, f"r{i}") for i in range(2)]
        runs.append(_with_id(OracleVerdict.FAIL, "rfail"))
        report = detector.detect(runs)
        self.assertEqual(report.category, WeaknessCategory.ISOLATED_FAILURE)
        self.assertEqual(report.fail_count, 1)


class TestRepeatedWeakness(unittest.TestCase):
    def test_two_failures_below_credible_threshold_is_repeated(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, "r1")]
        runs += [_with_id(OracleVerdict.FAIL, f"rf{i}") for i in range(2)]
        self.assertLess(len(runs), MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS)
        report = detector.detect(runs)
        self.assertEqual(report.category, WeaknessCategory.REPEATED_WEAKNESS)
        self.assertEqual(report.fail_count, 2)

    def test_low_failure_rate_above_sample_threshold_stays_repeated_not_credible(self) -> None:
        """Échantillon suffisant, mais taux d'échec SOUS le seuil heuristique
        — pas encore « statistiquement crédible »."""
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, f"rp{i}") for i in range(8)]
        runs.append(_with_id(OracleVerdict.FAIL, "rf1"))
        self.assertGreaterEqual(len(runs), MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS)
        report = detector.detect(runs)
        self.assertLess(report.failure_rate, CREDIBLE_FAILURE_RATE_THRESHOLD)
        self.assertEqual(report.category, WeaknessCategory.REPEATED_WEAKNESS)


class TestStatisticallyCredibleWeakness(unittest.TestCase):
    def test_high_failure_rate_on_sufficient_sample_is_credible(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.FAIL, f"rf{i}") for i in range(4)]
        runs.append(_with_id(OracleVerdict.PASS, "rp1"))
        self.assertGreaterEqual(len(runs), MIN_SAMPLE_FOR_CREDIBLE_WEAKNESS)
        report = detector.detect(runs)
        self.assertGreaterEqual(report.failure_rate, CREDIBLE_FAILURE_RATE_THRESHOLD)
        self.assertEqual(report.category, WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS)

    def test_reason_discloses_heuristic_not_a_real_stattest(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.FAIL, f"rf{i}") for i in range(5)]
        report = detector.detect(runs)
        self.assertIn("HEURISTIQUE", report.reason)
        self.assertIn("StatTest", report.reason)


class TestTraceabilityAndReproducibility(unittest.TestCase):
    def test_evidence_run_ids_reference_every_input_run(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, "ra"), _with_id(OracleVerdict.FAIL, "rb")]
        report = detector.detect(runs)
        self.assertEqual(report.evidence_run_ids, ("ra", "rb"))

    def test_same_input_always_yields_the_same_report(self) -> None:
        detector = WeaknessDetector()
        runs = [_with_id(OracleVerdict.PASS, "ra"), _with_id(OracleVerdict.FAIL, "rb")]
        first = detector.detect(runs)
        second = detector.detect(runs)
        self.assertEqual(first, second)

    def test_meaningful_sample_size_excludes_noise(self) -> None:
        detector = WeaknessDetector()
        runs = [
            _with_id(OracleVerdict.PASS, "r1"), _with_id(OracleVerdict.FAIL, "r2"),
            _with_id(OracleVerdict.ERROR, "r3"), _with_id(OracleVerdict.TIMEOUT, "r4"),
        ]
        report = detector.detect(runs)
        self.assertEqual(report.meaningful_sample_size, 2)


if __name__ == "__main__":
    unittest.main()
