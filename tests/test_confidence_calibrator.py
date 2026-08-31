"""Tests de ConfidenceCalibrator — intervalle de Wilson, aucune DB.

Vérifie les exigences explicites de la directive Phase 12 : absence de
données, faible nombre d'observations, calibration cohérente, surconfiance,
sous-confiance, convergence progressive, résultats contradictoires — et la
non-confusion déclarée/observée/calibrée.
"""

from __future__ import annotations

import unittest

from apex.calibration.calibrator import MIN_SAMPLE_FOR_CALIBRATION, ConfidenceCalibrator
from apex.calibration.model import CalibrationStatus
from apex.weakness.model import WeaknessCategory, WeaknessReport


class TestNoData(unittest.TestCase):
    def test_zero_observations_is_insufficient_data(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=0, total=0)
        self.assertEqual(result.status, CalibrationStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.observed_success_rate)
        self.assertIsNone(result.calibrated_point_estimate)


class TestFewObservations(unittest.TestCase):
    def test_below_minimum_sample_is_insufficient_data(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=1, total=MIN_SAMPLE_FOR_CALIBRATION - 1)
        self.assertEqual(result.status, CalibrationStatus.INSUFFICIENT_DATA)
        # La performance brute observée reste rapportée, même sans calibration.
        self.assertIsNotNone(result.observed_success_rate)
        self.assertIsNone(result.calibrated_point_estimate)

    def test_at_minimum_sample_is_calibrated(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=2, total=MIN_SAMPLE_FOR_CALIBRATION)
        self.assertEqual(result.status, CalibrationStatus.CALIBRATED)
        self.assertIsNotNone(result.calibrated_point_estimate)


class TestCoherentCalibration(unittest.TestCase):
    def test_calibrated_point_estimate_is_close_to_observed_rate_on_a_large_sample(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=80, total=100)
        self.assertAlmostEqual(result.calibrated_point_estimate, 0.8, delta=0.05)
        self.assertLess(result.calibrated_lower_bound, result.calibrated_point_estimate)
        self.assertGreater(result.calibrated_upper_bound, result.calibrated_point_estimate)

    def test_bounds_always_within_zero_and_one(self) -> None:
        calibrator = ConfidenceCalibrator()
        for successes, total in [(0, 3), (3, 3), (1, 3), (500, 1000)]:
            with self.subTest(successes=successes, total=total):
                result = calibrator.calibrate(successes=successes, total=total)
                self.assertGreaterEqual(result.calibrated_lower_bound, 0.0)
                self.assertLessEqual(result.calibrated_upper_bound, 1.0)
                self.assertLessEqual(result.calibrated_lower_bound, result.calibrated_upper_bound)

    def test_observed_rate_is_never_confused_with_calibrated_estimate(self) -> None:
        """Le taux brut et le centre de Wilson ne sont PAS identiques (le
        second est tiré vers 0.5 à petit échantillon) — jamais confondus."""
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=3, total=3)
        self.assertEqual(result.observed_success_rate, 1.0)
        self.assertLess(result.calibrated_point_estimate, 1.0)


class TestOverconfidenceAndUnderconfidence(unittest.TestCase):
    def test_declared_confidence_above_calibrated_is_overconfidence(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=10, total=100, declared_confidence=0.95)
        self.assertGreater(result.calibration_gap, 0.0)

    def test_declared_confidence_below_calibrated_is_underconfidence(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=90, total=100, declared_confidence=0.2)
        self.assertLess(result.calibration_gap, 0.0)

    def test_declared_confidence_is_echoed_back_verbatim_never_recomputed(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=5, total=10, declared_confidence=0.42)
        self.assertEqual(result.declared_confidence, 0.42)

    def test_no_declared_confidence_means_no_gap(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=5, total=10)
        self.assertIsNone(result.declared_confidence)
        self.assertIsNone(result.calibration_gap)

    def test_declared_confidence_out_of_range_rejected(self) -> None:
        calibrator = ConfidenceCalibrator()
        with self.assertRaises(ValueError):
            calibrator.calibrate(successes=5, total=10, declared_confidence=1.5)


class TestProgressiveConvergence(unittest.TestCase):
    def test_interval_width_shrinks_as_sample_size_grows_at_a_fixed_rate(self) -> None:
        calibrator = ConfidenceCalibrator()
        widths = []
        for total in (5, 50, 500):
            successes = round(total * 0.7)
            result = calibrator.calibrate(successes=successes, total=total)
            widths.append(result.calibrated_upper_bound - result.calibrated_lower_bound)
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_large_sample_point_estimate_converges_toward_true_rate(self) -> None:
        calibrator = ConfidenceCalibrator()
        small = calibrator.calibrate(successes=7, total=10)
        large = calibrator.calibrate(successes=700, total=1000)
        self.assertAlmostEqual(large.calibrated_point_estimate, 0.7, delta=0.03)
        # L'estimation à grand échantillon est plus proche du taux brut que
        # celle à petit échantillon (le lissage de Wilson pèse moins).
        self.assertLess(abs(large.calibrated_point_estimate - 0.7), abs(small.calibrated_point_estimate - 0.7))


class TestContradictoryResults(unittest.TestCase):
    def test_alternating_pass_fail_yields_a_stable_estimate_near_half(self) -> None:
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate(successes=5, total=10)
        self.assertAlmostEqual(result.calibrated_point_estimate, 0.5, delta=0.15)
        self.assertLessEqual(result.calibrated_lower_bound, 0.5)
        self.assertGreaterEqual(result.calibrated_upper_bound, 0.5)

    def test_never_raises_on_contradictory_or_edge_counts(self) -> None:
        calibrator = ConfidenceCalibrator()
        for successes, total in [(0, 3), (3, 3), (1, 2)]:
            with self.subTest(successes=successes, total=total):
                calibrator.calibrate(successes=successes, total=total)


class TestCalibrateFromWeaknessReport(unittest.TestCase):
    def test_uses_the_same_meaningful_sample_as_weakness_detector(self) -> None:
        """Zéro divergence de définition : PASS+FAIL uniquement, ERROR/
        TIMEOUT/UNKNOWN exclus — même échantillon que WeaknessDetector."""
        report = WeaknessReport(
            category=WeaknessCategory.NO_WEAKNESS, pass_count=8, fail_count=2, error_count=5,
            timeout_count=3, unknown_count=1, failure_rate=0.2, evidence_run_ids=(), reason="x",
        )
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate_from_weakness_report(report)
        self.assertEqual(result.sample_size, 10)

    def test_insufficient_weakness_data_propagates_to_insufficient_calibration(self) -> None:
        report = WeaknessReport(
            category=WeaknessCategory.INSUFFICIENT_DATA, pass_count=0, fail_count=0, error_count=2,
            timeout_count=0, unknown_count=0, failure_rate=None, evidence_run_ids=(), reason="x",
        )
        calibrator = ConfidenceCalibrator()
        result = calibrator.calibrate_from_weakness_report(report)
        self.assertEqual(result.status, CalibrationStatus.INSUFFICIENT_DATA)


class TestInputValidation(unittest.TestCase):
    def test_successes_greater_than_total_rejected(self) -> None:
        calibrator = ConfidenceCalibrator()
        with self.assertRaises(ValueError):
            calibrator.calibrate(successes=5, total=3)

    def test_negative_counts_rejected(self) -> None:
        calibrator = ConfidenceCalibrator()
        with self.assertRaises(ValueError):
            calibrator.calibrate(successes=-1, total=3)


if __name__ == "__main__":
    unittest.main()
