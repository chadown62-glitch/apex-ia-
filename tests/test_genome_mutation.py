"""Tests de apex.genome.mutation — mutate/crossover, purs, aucune DB."""

from __future__ import annotations

import unittest

from apex.genome.mutation import crossover, mutate


class TestMutateNeverModifiesInPlace(unittest.TestCase):
    def test_original_dict_is_untouched(self) -> None:
        original = {"x": 1.0}
        mutate(original, rate=1.0, seed=1)
        self.assertEqual(original, {"x": 1.0})


class TestMutateRateZeroIsIdentity(unittest.TestCase):
    def test_rate_zero_returns_an_equal_but_new_dict(self) -> None:
        genome = {"a": 1, "b": 2.5, "c": True, "d": "text", "e": {"f": 3}}
        result = mutate(genome, rate=0.0, seed=1)
        self.assertEqual(result, genome)
        self.assertIsNot(result, genome)


class TestMutateByType(unittest.TestCase):
    def test_strings_are_never_mutated(self) -> None:
        result = mutate({"name": "research"}, rate=1.0, seed=1)
        self.assertEqual(result["name"], "research")

    def test_none_is_never_mutated(self) -> None:
        result = mutate({"x": None}, rate=1.0, seed=1)
        self.assertIsNone(result["x"])

    def test_bool_can_flip_at_rate_one(self) -> None:
        flipped_at_least_once = any(
            mutate({"flag": True}, rate=1.0, seed=s)["flag"] is False for s in range(20)
        )
        self.assertTrue(flipped_at_least_once)

    def test_float_leaves_change_at_rate_one(self) -> None:
        result = mutate({"x": 1.0}, rate=1.0, seed=1, magnitude=0.5)
        self.assertNotEqual(result["x"], 1.0)

    def test_float_stays_within_magnitude_bound(self) -> None:
        for seed in range(50):
            result = mutate({"x": 100.0}, rate=1.0, seed=seed, magnitude=0.1)
            self.assertGreaterEqual(result["x"], 100.0 * 0.9)
            self.assertLessEqual(result["x"], 100.0 * 1.1)

    def test_int_leaves_stay_int_type(self) -> None:
        result = mutate({"x": 10}, rate=1.0, seed=1, magnitude=0.5)
        self.assertIsInstance(result["x"], int)
        self.assertNotIsInstance(result["x"], bool)

    def test_nested_dicts_and_lists_are_mutated_recursively(self) -> None:
        genome = {"outer": {"inner": [1.0, 2.0, 3.0]}}
        result = mutate(genome, rate=1.0, seed=1, magnitude=0.5)
        self.assertNotEqual(result["outer"]["inner"], [1.0, 2.0, 3.0])


class TestMutateDeterminism(unittest.TestCase):
    def test_same_seed_same_genome_yields_identical_result(self) -> None:
        genome = {"a": 1.0, "b": {"c": 2, "d": True}}
        first = mutate(genome, rate=0.5, seed=42)
        second = mutate(genome, rate=0.5, seed=42)
        self.assertEqual(first, second)

    def test_different_seeds_can_yield_different_results(self) -> None:
        genome = {"a": 1.0}
        results = {mutate(genome, rate=1.0, seed=s)["a"] for s in range(10)}
        self.assertGreater(len(results), 1)


class TestMutateInputValidation(unittest.TestCase):
    def test_rate_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mutate({"x": 1.0}, rate=1.5, seed=1)
        with self.assertRaises(ValueError):
            mutate({"x": 1.0}, rate=-0.1, seed=1)

    def test_negative_magnitude_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mutate({"x": 1.0}, rate=0.5, seed=1, magnitude=-0.1)


class TestCrossoverNeverModifiesParents(unittest.TestCase):
    def test_parents_are_untouched(self) -> None:
        a = {"x": 1}
        b = {"x": 2}
        crossover(a, b, seed=1)
        self.assertEqual(a, {"x": 1})
        self.assertEqual(b, {"x": 2})


class TestCrossoverInheritance(unittest.TestCase):
    def test_key_only_in_one_parent_is_inherited_directly(self) -> None:
        a = {"only_a": 1}
        b = {"only_b": 2}
        child = crossover(a, b, seed=1)
        self.assertEqual(child, {"only_a": 1, "only_b": 2})

    def test_shared_key_comes_from_either_parent_across_seeds(self) -> None:
        a = {"x": "from_a"}
        b = {"x": "from_b"}
        values = {crossover(a, b, seed=s)["x"] for s in range(20)}
        self.assertEqual(values, {"from_a", "from_b"})

    def test_nested_dicts_are_crossed_recursively(self) -> None:
        a = {"outer": {"only_a": 1, "shared": "a"}}
        b = {"outer": {"only_b": 2, "shared": "b"}}
        child = crossover(a, b, seed=1)
        self.assertEqual(child["outer"]["only_a"], 1)
        self.assertEqual(child["outer"]["only_b"], 2)
        self.assertIn(child["outer"]["shared"], ("a", "b"))


class TestCrossoverDeterminism(unittest.TestCase):
    def test_same_seed_same_parents_yields_identical_child(self) -> None:
        a = {"x": 1, "y": {"z": "a"}}
        b = {"x": 2, "y": {"z": "b"}}
        first = crossover(a, b, seed=7)
        second = crossover(a, b, seed=7)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
