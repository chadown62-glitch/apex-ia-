"""Tests de CurriculumStrategy — décision pure, déterministe, aucune DB.

Vérifie les exigences explicites de la directive Phase 12 : progression
déterministe, faiblesse détectée, absence de faiblesse, difficulté
croissante, cas limite (bornes min/max), aucun saut incohérent de difficulté.
"""

from __future__ import annotations

import unittest

from apex.curriculum.model import CurriculumDirection
from apex.curriculum.strategy import CurriculumStrategy
from apex.weakness.model import WeaknessCategory, WeaknessReport


def a_report(category: WeaknessCategory, **overrides) -> WeaknessReport:
    defaults = dict(
        pass_count=1, fail_count=0, error_count=0, timeout_count=0, unknown_count=0,
        failure_rate=0.0, evidence_run_ids=("r1",), reason="test",
    )
    defaults.update(overrides)
    return WeaknessReport(category=category, **defaults)


class TestNoWeaknessAdvances(unittest.TestCase):
    def test_no_weakness_advances_one_level(self) -> None:
        strategy = CurriculumStrategy()
        decision = strategy.decide(
            specialty="research", current_level=3, weakness_report=a_report(WeaknessCategory.NO_WEAKNESS),
        )
        self.assertEqual(decision.direction, CurriculumDirection.ADVANCE)
        self.assertEqual(decision.recommended_level, 4)

    def test_no_weakness_never_jumps_more_than_one_level(self) -> None:
        strategy = CurriculumStrategy(min_level=1, max_level=100)
        decision = strategy.decide(
            specialty="research", current_level=50, weakness_report=a_report(WeaknessCategory.NO_WEAKNESS),
        )
        self.assertEqual(decision.recommended_level - decision.current_level, 1)


class TestWeaknessDetectedReinforces(unittest.TestCase):
    def test_credible_weakness_reinforces_one_level_down(self) -> None:
        strategy = CurriculumStrategy()
        decision = strategy.decide(
            specialty="research", current_level=5,
            weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertEqual(decision.direction, CurriculumDirection.REINFORCE)
        self.assertEqual(decision.recommended_level, 4)

    def test_reinforce_never_jumps_more_than_one_level(self) -> None:
        strategy = CurriculumStrategy(min_level=1, max_level=100)
        decision = strategy.decide(
            specialty="research", current_level=50,
            weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertEqual(decision.current_level - decision.recommended_level, 1)


class TestHoldingCategories(unittest.TestCase):
    def test_insufficient_data_holds(self) -> None:
        strategy = CurriculumStrategy()
        decision = strategy.decide(
            specialty="research", current_level=3,
            weakness_report=a_report(WeaknessCategory.INSUFFICIENT_DATA, failure_rate=None),
        )
        self.assertEqual(decision.direction, CurriculumDirection.HOLD)
        self.assertEqual(decision.recommended_level, 3)

    def test_isolated_failure_holds_not_reinforce(self) -> None:
        """Un échec isolé ne doit JAMAIS provoquer un recul — sinon une
        simple malchance dégraderait le curriculum à tort."""
        strategy = CurriculumStrategy()
        decision = strategy.decide(
            specialty="research", current_level=3,
            weakness_report=a_report(WeaknessCategory.ISOLATED_FAILURE, fail_count=1, failure_rate=1.0),
        )
        self.assertEqual(decision.direction, CurriculumDirection.HOLD)
        self.assertEqual(decision.recommended_level, 3)

    def test_repeated_weakness_holds_not_reinforce_yet(self) -> None:
        """Répété mais pas encore statistiquement crédible : prudence, pas
        de recul prématuré."""
        strategy = CurriculumStrategy()
        decision = strategy.decide(
            specialty="research", current_level=3,
            weakness_report=a_report(WeaknessCategory.REPEATED_WEAKNESS, fail_count=2, failure_rate=0.4),
        )
        self.assertEqual(decision.direction, CurriculumDirection.HOLD)


class TestBoundaryClamping(unittest.TestCase):
    def test_cannot_advance_past_max_level(self) -> None:
        strategy = CurriculumStrategy(min_level=1, max_level=5)
        decision = strategy.decide(
            specialty="research", current_level=5, weakness_report=a_report(WeaknessCategory.NO_WEAKNESS),
        )
        self.assertEqual(decision.recommended_level, 5)

    def test_cannot_reinforce_below_min_level(self) -> None:
        strategy = CurriculumStrategy(min_level=1, max_level=5)
        decision = strategy.decide(
            specialty="research", current_level=1,
            weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertEqual(decision.recommended_level, 1)

    def test_current_level_out_of_bounds_raises(self) -> None:
        strategy = CurriculumStrategy(min_level=1, max_level=5)
        with self.assertRaises(ValueError):
            strategy.decide(
                specialty="research", current_level=6, weakness_report=a_report(WeaknessCategory.NO_WEAKNESS),
            )

    def test_min_level_below_one_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            CurriculumStrategy(min_level=0)

    def test_max_below_min_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            CurriculumStrategy(min_level=5, max_level=1)


class TestDeterminism(unittest.TestCase):
    def test_same_report_always_yields_the_same_decision(self) -> None:
        strategy = CurriculumStrategy()
        report = a_report(WeaknessCategory.NO_WEAKNESS)
        first = strategy.decide(specialty="research", current_level=3, weakness_report=report)
        second = strategy.decide(specialty="research", current_level=3, weakness_report=report)
        self.assertEqual(first, second)

    def test_decision_carries_the_weakness_report_for_traceability(self) -> None:
        strategy = CurriculumStrategy()
        report = a_report(WeaknessCategory.NO_WEAKNESS)
        decision = strategy.decide(specialty="research", current_level=3, weakness_report=report)
        self.assertIs(decision.weakness_report, report)


if __name__ == "__main__":
    unittest.main()
