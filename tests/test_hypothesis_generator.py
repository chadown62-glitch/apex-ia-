"""Tests de HypothesisGenerator — génération pure, aucune DB, aucun modèle.

Vérifie : rien à expliquer sans faiblesse (NO_WEAKNESS/INSUFFICIENT_DATA
rejetés), une hypothèse falsifiable est produite pour chaque catégorie de
faiblesse réelle, la traçabilité vers le WeaknessReport source, et que le
chemin de réfutation proposé nomme des composants réels (TaskGenerator/
StatTest) sans jamais les exécuter.
"""

from __future__ import annotations

import unittest

from apex.hypothesis.generator import HypothesisGenerator
from apex.weakness.model import WeaknessCategory, WeaknessReport


def a_report(category: WeaknessCategory, **overrides) -> WeaknessReport:
    defaults = dict(
        pass_count=2, fail_count=3, error_count=0, timeout_count=0, unknown_count=0,
        failure_rate=0.6, evidence_run_ids=("r1", "r2"), reason="test reason",
    )
    defaults.update(overrides)
    return WeaknessReport(category=category, **defaults)


class TestNothingToExplain(unittest.TestCase):
    def test_no_weakness_is_rejected(self) -> None:
        generator = HypothesisGenerator()
        with self.assertRaises(ValueError):
            generator.generate(
                specialty="research", weakness_report=a_report(WeaknessCategory.NO_WEAKNESS, failure_rate=0.0),
            )

    def test_insufficient_data_is_rejected(self) -> None:
        generator = HypothesisGenerator()
        with self.assertRaises(ValueError):
            generator.generate(
                specialty="research",
                weakness_report=a_report(WeaknessCategory.INSUFFICIENT_DATA, failure_rate=None),
            )


class TestGeneratesAFalsifiableHypothesis(unittest.TestCase):
    def test_isolated_failure_produces_a_hypothesis(self) -> None:
        generator = HypothesisGenerator()
        hyp = generator.generate(specialty="research", weakness_report=a_report(WeaknessCategory.ISOLATED_FAILURE))
        self.assertEqual(hyp.specialty, "research")
        self.assertTrue(hyp.claim)
        self.assertTrue(hyp.prediction)
        self.assertTrue(hyp.proposed_falsification)

    def test_credible_weakness_produces_a_hypothesis(self) -> None:
        generator = HypothesisGenerator()
        hyp = generator.generate(
            specialty="research", weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertIn("research", hyp.claim)

    def test_prediction_names_the_current_failure_rate_when_available(self) -> None:
        generator = HypothesisGenerator()
        hyp = generator.generate(
            specialty="research",
            weakness_report=a_report(WeaknessCategory.REPEATED_WEAKNESS, failure_rate=0.6),
        )
        self.assertIn("60%", hyp.prediction)

    def test_traceable_to_the_source_weakness_report(self) -> None:
        generator = HypothesisGenerator()
        report = a_report(WeaknessCategory.REPEATED_WEAKNESS)
        hyp = generator.generate(specialty="research", weakness_report=report)
        self.assertIs(hyp.source_weakness_report, report)

    def test_each_hypothesis_gets_a_distinct_id(self) -> None:
        generator = HypothesisGenerator()
        report = a_report(WeaknessCategory.REPEATED_WEAKNESS)
        a = generator.generate(specialty="research", weakness_report=report)
        b = generator.generate(specialty="research", weakness_report=report)
        self.assertNotEqual(a.id, b.id)


class TestFalsificationPathNamesRealComponentsWithoutExecutingThem(unittest.TestCase):
    def test_falsification_names_taskgen_and_stattest(self) -> None:
        generator = HypothesisGenerator()
        hyp = generator.generate(
            specialty="research", weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertIn("TARGETED_WEAKNESS", hyp.proposed_falsification)
        self.assertIn("StatTest", hyp.proposed_falsification)

    def test_generator_itself_never_imports_taskgen_or_evalrunner_or_agentruntime(self) -> None:
        """Preuve que ce module NOMME les composants sans les exécuter :
        aucune dépendance d'exécution réelle n'est importée."""
        import apex.hypothesis.generator as module
        import inspect

        source = inspect.getsource(module)
        for forbidden in ("apex.evalrunner", "apex.agents.runtime", "apex.sandbox", "AgentRuntime"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
