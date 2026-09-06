"""Tests de DeterministicTaskGenerator — génération pure, aucune DB, aucune
sandbox. Vérifie les modes réels et le refus honnête (NotImplementedYet)
des modes sans backend réel."""

from __future__ import annotations

import subprocess
import unittest

from apex.errors import NotImplementedYet
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import GenerationMode
from apex.tasks.model import Task, TaskSplit
from apex.weakness.model import WeaknessCategory, WeaknessReport


def a_report(category: WeaknessCategory) -> WeaknessReport:
    return WeaknessReport(
        category=category, pass_count=1, fail_count=1, error_count=0, timeout_count=0,
        unknown_count=0, failure_rate=0.5, evidence_run_ids=("r1",), reason="test",
    )


class TestNormalMode(unittest.TestCase):
    def test_generates_the_baseline_research_diagnostic(self) -> None:
        generator = DeterministicTaskGenerator()
        spec = generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        self.assertEqual(spec.mode, GenerationMode.NORMAL)
        self.assertEqual(spec.specialty, "research")
        self.assertEqual(spec.split, TaskSplit.TRAIN)

    def test_command_is_real_and_runs_without_error(self) -> None:
        generator = DeterministicTaskGenerator()
        spec = generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        result = subprocess.run(list(spec.command), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)

    def test_split_is_honored(self) -> None:
        generator = DeterministicTaskGenerator()
        spec = generator.generate(mode=GenerationMode.NORMAL, specialty="research", split=TaskSplit.HELDOUT)
        self.assertEqual(spec.split, TaskSplit.HELDOUT)


class TestLevelBasedModes(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = DeterministicTaskGenerator()

    def test_increasing_difficulty_requires_a_level(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research")

    def test_level_out_of_bounds_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=0)
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=4)

    def test_different_levels_produce_genuinely_different_commands(self) -> None:
        commands = {
            level: self.generator.generate(
                mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=level,
            ).command
            for level in (1, 2, 3)
        }
        self.assertNotEqual(commands[1], commands[2])
        self.assertNotEqual(commands[2], commands[3])

    def test_level_1_and_2_never_fail_on_their_own_observations(self) -> None:
        """Niveaux 1/2 observent seulement, jamais d'assertion — toujours
        exit 0, quel que soit l'environnement réseau réel de ce test."""
        for level in (1, 2):
            with self.subTest(level=level):
                spec = self.generator.generate(
                    mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=level,
                )
                result = subprocess.run(list(spec.command), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0)

    def test_level_3_asserts_and_can_genuinely_fail(self) -> None:
        """Preuve que la « difficulté » n'est pas cosmétique : le niveau 3
        échoue réellement (exit != 0) quand l'isolation réseau attendue
        n'est PAS respectée — ce qui est le cas ici (hors sandbox)."""
        spec = self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=3)
        result = subprocess.run(list(spec.command), capture_output=True, text=True, timeout=10)
        # Hors sandbox, le réseau EST joignable -> l'assertion doit échouer.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ECHEC", result.stderr)

    def test_curriculum_intermediate_uses_the_same_mechanism_with_its_own_mode_tag(self) -> None:
        spec = self.generator.generate(
            mode=GenerationMode.CURRICULUM_INTERMEDIATE, specialty="research", level=2,
        )
        self.assertEqual(spec.mode, GenerationMode.CURRICULUM_INTERMEDIATE)
        other = self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=2)
        self.assertEqual(spec.command, other.command)


class TestTargetedWeakness(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = DeterministicTaskGenerator()

    def test_no_weakness_has_nothing_to_target(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(
                mode=GenerationMode.TARGETED_WEAKNESS, specialty="research",
                weakness_report=a_report(WeaknessCategory.NO_WEAKNESS),
            )

    def test_insufficient_data_has_nothing_to_target(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(
                mode=GenerationMode.TARGETED_WEAKNESS, specialty="research",
                weakness_report=a_report(WeaknessCategory.INSUFFICIENT_DATA),
            )

    def test_missing_weakness_report_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.TARGETED_WEAKNESS, specialty="research")

    def test_credible_weakness_generates_the_hardest_level(self) -> None:
        spec = self.generator.generate(
            mode=GenerationMode.TARGETED_WEAKNESS, specialty="research",
            weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        expected = self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=3)
        self.assertEqual(spec.command, expected.command)

    def test_isolated_failure_generates_a_lighter_level_than_credible_weakness(self) -> None:
        isolated = self.generator.generate(
            mode=GenerationMode.TARGETED_WEAKNESS, specialty="research",
            weakness_report=a_report(WeaknessCategory.ISOLATED_FAILURE),
        )
        credible = self.generator.generate(
            mode=GenerationMode.TARGETED_WEAKNESS, specialty="research",
            weakness_report=a_report(WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS),
        )
        self.assertNotEqual(isolated.command, credible.command)


class TestRegression(unittest.TestCase):
    def test_reproduces_the_base_task_command_exactly(self) -> None:
        generator = DeterministicTaskGenerator()
        base = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="original", description="d",
            command=("python3", "-c", "import sys; sys.exit(0)"), created_by="human",
        )
        spec = generator.generate(mode=GenerationMode.REGRESSION, specialty="research", base_task=base)
        self.assertEqual(spec.command, base.command)
        self.assertEqual(spec.dataset_ref, base.id)

    def test_missing_base_task_rejected(self) -> None:
        generator = DeterministicTaskGenerator()
        with self.assertRaises(ValueError):
            generator.generate(mode=GenerationMode.REGRESSION, specialty="research")


class TestUnsupportedModesAreHonest(unittest.TestCase):
    """Aucun mode sans backend réel ne doit produire de contenu — chacun
    lève NotImplementedYet, jamais un script inventé."""

    def test_all_unsupported_modes_raise_not_implemented_yet(self) -> None:
        """`EDGE_CASE`/`ADVERSARIAL` sont RÉELS depuis N+10 (voir
        `tests/test_taskgen_edge_case_adversarial.py`) — retirés d'ici,
        jamais testés deux fois pour le même comportement."""
        generator = DeterministicTaskGenerator()
        for mode in (
            GenerationMode.BUG_FARM, GenerationMode.TRANSFER,
            GenerationMode.SELF_PLAY, GenerationMode.FROM_HYPOTHESIS,
        ):
            with self.subTest(mode=mode), self.assertRaises(NotImplementedYet):
                generator.generate(mode=mode, specialty="research")

    def test_unsupported_specialty_raises_not_implemented_yet(self) -> None:
        generator = DeterministicTaskGenerator()
        with self.assertRaises(NotImplementedYet):
            generator.generate(mode=GenerationMode.NORMAL, specialty="code-intelligence")


if __name__ == "__main__":
    unittest.main()
