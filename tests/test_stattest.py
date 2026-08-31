"""Tests de StatTest — bootstrap apparié, aucune DB, aucun réseau.

Vérifie : données insuffisantes, différence claire détectée comme
significative, absence de différence détectée comme non significative,
reproductibilité (même graine -> même résultat), validation des entrées,
et « preuve cumulable » (ré-exécution avec plus de paires reste valide,
aucun état conservé entre deux appels).
"""

from __future__ import annotations

import unittest

from apex.stattest.comparator import MIN_PAIRS, StatTest
from apex.stattest.model import StatTestVerdict


class TestInsufficientData(unittest.TestCase):
    def test_below_minimum_pairs_is_insufficient_data(self) -> None:
        test = StatTest(seed=1)
        result = test.compare_paired([1.0] * (MIN_PAIRS - 1), [0.0] * (MIN_PAIRS - 1))
        self.assertEqual(result.verdict, StatTestVerdict.INSUFFICIENT_DATA)
        self.assertIsNone(result.observed_diff)
        self.assertIsNone(result.ci_lower)

    def test_at_minimum_pairs_a_decision_is_attempted(self) -> None:
        test = StatTest(seed=1)
        result = test.compare_paired([1.0] * MIN_PAIRS, [0.0] * MIN_PAIRS)
        self.assertNotEqual(result.verdict, StatTestVerdict.INSUFFICIENT_DATA)


class TestClearDifferenceIsSignificant(unittest.TestCase):
    def test_constant_maximal_difference_is_significant(self) -> None:
        test = StatTest(seed=1)
        a = [0.0] * 20
        b = [1.0] * 20
        result = test.compare_paired(a, b)
        self.assertEqual(result.verdict, StatTestVerdict.SIGNIFICANT)
        self.assertEqual(result.observed_diff, 1.0)
        self.assertGreater(result.ci_lower, 0.0)

    def test_large_consistent_gap_is_significant(self) -> None:
        test = StatTest(seed=7)
        a = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        b = [1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0]
        result = test.compare_paired(a, b)
        self.assertEqual(result.verdict, StatTestVerdict.SIGNIFICANT)


class TestNoDifferenceIsNotSignificant(unittest.TestCase):
    def test_identical_samples_are_not_significant(self) -> None:
        test = StatTest(seed=1)
        a = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        result = test.compare_paired(a, list(a))
        self.assertEqual(result.verdict, StatTestVerdict.NOT_SIGNIFICANT)
        self.assertEqual(result.observed_diff, 0.0)

    def test_noisy_small_difference_within_a_small_sample_is_not_significant(self) -> None:
        test = StatTest(seed=1)
        a = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        b = [1.0, 0.0, 1.0, 0.0, 0.0, 1.0]  # une seule paire diffère
        result = test.compare_paired(a, b)
        self.assertEqual(result.verdict, StatTestVerdict.NOT_SIGNIFICANT)


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_data_yields_identical_result(self) -> None:
        a = [1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0]
        b = [0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 0.0]
        first = StatTest(seed=42).compare_paired(a, b)
        second = StatTest(seed=42).compare_paired(a, b)
        self.assertEqual(first, second)

    def test_different_seeds_may_produce_slightly_different_bounds_but_same_verdict_on_a_clear_signal(self) -> None:
        a = [0.0] * 20
        b = [1.0] * 20
        r1 = StatTest(seed=1).compare_paired(a, b)
        r2 = StatTest(seed=2).compare_paired(a, b)
        self.assertEqual(r1.verdict, r2.verdict)


class TestCumulativeProof(unittest.TestCase):
    """Principe 10 : ré-exécuter avec un échantillon plus grand reste une
    décision valide et indépendante, jamais un état accumulé entre appels."""

    def test_growing_the_sample_can_change_the_verdict_without_any_shared_state(self) -> None:
        test = StatTest(seed=3)
        a_small = [0.0, 1.0, 0.0, 1.0, 0.0]
        b_small = [1.0, 1.0, 0.0, 1.0, 1.0]
        small_result = test.compare_paired(a_small, b_small)

        a_large = a_small * 6
        b_large = b_small * 6
        large_result = test.compare_paired(a_large, b_large)

        # Même StatTest, deux appels indépendants — la seconde décision ne
        # dépend en rien de la première.
        self.assertEqual(large_result.sample_size, len(a_large))
        self.assertEqual(small_result.sample_size, len(a_small))


class TestInputValidation(unittest.TestCase):
    def test_mismatched_lengths_rejected(self) -> None:
        test = StatTest(seed=1)
        with self.assertRaises(ValueError):
            test.compare_paired([1.0, 0.0], [1.0, 0.0, 1.0])

    def test_n_bootstrap_too_low_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            StatTest(n_bootstrap=10)

    def test_confidence_level_out_of_range_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            StatTest(confidence_level=1.0)
        with self.assertRaises(ValueError):
            StatTest(confidence_level=0.0)


if __name__ == "__main__":
    unittest.main()
