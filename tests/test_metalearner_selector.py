"""Tests de MetaLearner (v1, politique statique) — aucune DB, aucun réseau.

Vérifie : composition réelle de CurriculumStrategy (jamais une seconde
logique de progression), réutilisation directe de GenerationMode (jamais
un second vocabulaire de stratégie), aucune stratégie recommandée sans
signal (INSUFFICIENT_DATA), déterminisme, et absence de toute prétention
d'apprentissage (aucun état conservé entre deux appels).
"""

from __future__ import annotations

import unittest

from apex.curriculum.model import CurriculumDirection
from apex.metalearner.selector import MetaLearner
from apex.taskgen.model import GenerationMode
from apex.weakness.model import WeaknessCategory, WeaknessReport


def a_report(category: WeaknessCategory, **overrides) -> WeaknessReport:
    defaults = dict(
        pass_count=1, fail_count=1, error_count=0, timeout_count=0, unknown_count=0,
        failure_rate=0.5, evidence_run_ids=("r1",), reason="test",
    )
    defaults.update(overrides)
    return WeaknessReport(category=category, **defaults)


class TestInsufficientDataRecommendsNoStrategy(unittest.TestCase):
    def test_no_mode_recommended_without_signal(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.INSUFFICIENT_DATA, failure_rate=None),
            current_level=3,
        )
        self.assertIsNone(decision.mode)

    def test_curriculum_still_holds_the_level(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.INSUFFICIENT_DATA, failure_rate=None),
            current_level=3,
        )
        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.curriculum_decision.direction, CurriculumDirection.HOLD)


class TestNoWeaknessAdvances(unittest.TestCase):
    def test_recommends_curriculum_intermediate_at_the_next_level(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.NO_WEAKNESS, failure_rate=0.0),
            current_level=3,
        )
        self.assertEqual(decision.mode, GenerationMode.CURRICULUM_INTERMEDIATE)
        self.assertEqual(decision.level, 4)


class TestWeaknessDetectedTargetsReinforcement(unittest.TestCase):
    def test_isolated_failure_recommends_targeted_weakness_holding_level(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.ISOLATED_FAILURE), current_level=3,
        )
        self.assertEqual(decision.mode, GenerationMode.TARGETED_WEAKNESS)
        self.assertEqual(decision.level, 3)

    def test_repeated_weakness_recommends_targeted_weakness(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.REPEATED_WEAKNESS), current_level=3,
        )
        self.assertEqual(decision.mode, GenerationMode.TARGETED_WEAKNESS)

    def test_credible_weakness_recommends_targeted_weakness_and_reinforces_level(self) -> None:
        learner = MetaLearner()
        decision = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
            current_level=3,
        )
        self.assertEqual(decision.mode, GenerationMode.TARGETED_WEAKNESS)
        self.assertEqual(decision.level, 2)
        self.assertEqual(decision.curriculum_decision.direction, CurriculumDirection.REINFORCE)


class TestComposesCurriculumStrategyRatherThanDuplicating(unittest.TestCase):
    def test_decision_carries_the_real_curriculum_decision(self) -> None:
        learner = MetaLearner()
        report = a_report(WeaknessCategory.NO_WEAKNESS, failure_rate=0.0)
        decision = learner.select_strategy(specialty="research", weakness_report=report, current_level=5)
        self.assertIs(decision.curriculum_decision.weakness_report, report)

    def test_level_always_matches_curriculum_decisions_recommended_level(self) -> None:
        learner = MetaLearner()
        for category in WeaknessCategory:
            with self.subTest(category=category):
                report = a_report(category, failure_rate=(None if category.value == "insufficient_data" else 0.3))
                decision = learner.select_strategy(specialty="research", weakness_report=report, current_level=4)
                self.assertEqual(decision.level, decision.curriculum_decision.recommended_level)


class TestDeterminismAndStatelessness(unittest.TestCase):
    def test_same_inputs_always_yield_the_same_decision(self) -> None:
        learner = MetaLearner()
        report = a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS)
        first = learner.select_strategy(specialty="research", weakness_report=report, current_level=3)
        second = learner.select_strategy(specialty="research", weakness_report=report, current_level=3)
        self.assertEqual(first, second)

    def test_no_state_is_carried_between_calls(self) -> None:
        """Preuve d'absence d'apprentissage : deux appels indépendants avec
        des faiblesses opposées donnent chacun leur propre décision
        cohérente, jamais influencée par l'appel précédent."""
        learner = MetaLearner()
        credible = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
            current_level=5,
        )
        self.assertEqual(credible.level, 4)
        no_weakness = learner.select_strategy(
            specialty="research", weakness_report=a_report(WeaknessCategory.NO_WEAKNESS, failure_rate=0.0),
            current_level=5,
        )
        self.assertEqual(no_weakness.level, 6)


if __name__ == "__main__":
    unittest.main()
