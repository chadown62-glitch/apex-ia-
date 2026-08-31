"""Tests de apex.genome.selection — successive_halving/pareto_front, purs."""

from __future__ import annotations

import unittest

from apex.genome.selection import pareto_front, successive_halving


class TestSuccessiveHalving(unittest.TestCase):
    def test_empty_scores_returns_empty(self) -> None:
        self.assertEqual(successive_halving({}), ())

    def test_keeps_the_higher_scoring_half(self) -> None:
        result = successive_halving({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}, keep_fraction=0.5)
        self.assertEqual(set(result), {"c", "d"})

    def test_single_candidate_always_kept(self) -> None:
        result = successive_halving({"only": 1.0}, keep_fraction=0.5)
        self.assertEqual(result, ("only",))

    def test_at_least_one_survivor_even_with_tiny_fraction(self) -> None:
        result = successive_halving({"a": 1.0, "b": 2.0, "c": 3.0}, keep_fraction=0.01)
        self.assertEqual(len(result), 1)
        self.assertEqual(result, ("c",))

    def test_order_is_best_first(self) -> None:
        result = successive_halving({"a": 5.0, "b": 1.0, "c": 3.0}, keep_fraction=1.0)
        self.assertEqual(result, ("a", "c", "b"))

    def test_keep_fraction_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            successive_halving({"a": 1.0}, keep_fraction=0.0)
        with self.assertRaises(ValueError):
            successive_halving({"a": 1.0}, keep_fraction=1.5)

    def test_deterministic_tie_break_by_insertion_order(self) -> None:
        scores = {"a": 1.0, "b": 1.0, "c": 1.0}
        first = successive_halving(scores, keep_fraction=1.0)
        second = successive_halving(scores, keep_fraction=1.0)
        self.assertEqual(first, second)


class TestParetoFront(unittest.TestCase):
    def test_empty_objectives_returns_empty(self) -> None:
        self.assertEqual(pareto_front({}), frozenset())

    def test_single_dominating_candidate_is_the_whole_front(self) -> None:
        result = pareto_front({"a": (1, 2), "b": (2, 1), "c": (0, 0), "d": (2, 2)})
        self.assertEqual(result, frozenset({"d"}))

    def test_non_dominated_tradeoffs_all_survive(self) -> None:
        """a est meilleur sur le 1er objectif, b sur le 2e — aucun ne domine l'autre."""
        result = pareto_front({"a": (2, 1), "b": (1, 2)})
        self.assertEqual(result, frozenset({"a", "b"}))

    def test_strictly_dominated_candidate_is_excluded(self) -> None:
        result = pareto_front({"a": (2, 2), "b": (1, 1)})
        self.assertEqual(result, frozenset({"a"}))

    def test_identical_scores_both_survive_no_strict_domination(self) -> None:
        result = pareto_front({"a": (1, 1), "b": (1, 1)})
        self.assertEqual(result, frozenset({"a", "b"}))

    def test_mismatched_objective_counts_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pareto_front({"a": (1, 2), "b": (1,)})

    def test_single_objective_reduces_to_the_maximum(self) -> None:
        result = pareto_front({"a": (1,), "b": (3,), "c": (2,)})
        self.assertEqual(result, frozenset({"b"}))


if __name__ == "__main__":
    unittest.main()
