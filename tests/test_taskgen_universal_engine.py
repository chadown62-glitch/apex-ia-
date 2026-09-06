"""Tests du Universal Task Engine (passe « Universal Task + Curriculum
Engine », N+11) — `apex.taskgen.task_factory`/`specialty_provider`/
`registry`/`variant_engine`/`difficulty`. Lettres A/B/C/D/E/F/G/I/J/K/L/M/
N/U/V/W/X du §21 de la mission citées explicitement. Aucune DB requise :
ces tests portent sur la couche pure `apex.taskgen`."""

from __future__ import annotations

import unittest

from apex.errors import NotImplementedYet
from apex.sandbox.model import ResourceLimits
from apex.taskgen.difficulty import Difficulty, clamp_to_ceiling, easier_than, from_int, harder_than
from apex.taskgen.model import (
    AdversarialKind,
    EdgeCaseKind,
    GeneralizationVariant,
    TaskConstraints,
    TaskKind,
    TaskProvenance,
)
from apex.taskgen.registry import get_provider, supported_specialty_ids
from apex.taskgen.specialty_provider import ResearchTaskProvider
from apex.taskgen.task_factory import TaskSpec, UniversalTaskFactory
from apex.taskgen.variant_engine import VariantEngine, VariantRequest
from apex.tasks.model import Task, TaskSplit


def a_limits(**overrides) -> ResourceLimits:
    defaults = dict(cpu_cores=1.0, memory_mb=256, pids_limit=32, timeout_s=10.0, disk_mb=64, network="none")
    defaults.update(overrides)
    return ResourceLimits(**defaults)


def a_task(**overrides) -> Task:
    defaults = dict(
        split=TaskSplit.TRAIN, specialty="research", title="base", description="base task",
        command=("python3", "-c", "print(1)"), created_by="human",
    )
    defaults.update(overrides)
    return Task.new(**defaults)


class TestTaskFactoryContract(unittest.TestCase):
    """Lettre A du §21 — `TaskFactory` (`UniversalTaskFactory`) est le
    point d'entrée UNIQUE, quelle que soit la spécialité."""

    def test_create_delegates_to_the_real_specialty_provider(self) -> None:
        factory = UniversalTaskFactory()
        generated = factory.create(TaskSpec(kind=TaskKind.NORMAL, specialty="research"))
        self.assertEqual(generated.specialty, "research")
        self.assertIsNotNone(generated.provenance)
        self.assertEqual(generated.provenance.generator_version, "1")

    def test_unknown_specialty_raises_not_implemented_yet_never_a_fabricated_task(self) -> None:
        factory = UniversalTaskFactory()
        with self.assertRaises(NotImplementedYet):
            factory.create(TaskSpec(kind=TaskKind.NORMAL, specialty="design-studio"))

    def test_provenance_carries_weakness_and_challenge_ids_when_supplied(self) -> None:
        factory = UniversalTaskFactory()
        generated = factory.create(
            TaskSpec(kind=TaskKind.NORMAL, specialty="research", weakness_id="research:security:x"),
        )
        self.assertEqual(generated.provenance.weakness_id, "research:security:x")


class TestTaskKindUnification(unittest.TestCase):
    """Lettre B du §21 — `TaskKind` est le vocabulaire UNIFIÉ, réutilisé
    tel quel par `apex.metalearner.challenge_model.ChallengeSource` (alias
    direct, jamais un doublon conceptuel)."""

    def test_challenge_source_is_literally_task_kind(self) -> None:
        from apex.metalearner.challenge_model import ChallengeSource

        self.assertIs(ChallengeSource, TaskKind)

    def test_all_seven_mission_kinds_are_present(self) -> None:
        expected = {
            "normal", "edge_case", "adversarial", "security", "generalization", "regression", "bug_farm",
        }
        self.assertTrue(expected.issubset({k.value for k in TaskKind}))


class TestSpecialtyTaskProviderContract(unittest.TestCase):
    """Lettre C du §21 — une specialty déclare ce qu'elle sait produire ;
    le moteur ne prétend jamais générer une capacité non supportée."""

    def test_research_provider_is_registered(self) -> None:
        provider = get_provider("research")
        self.assertIsInstance(provider, ResearchTaskProvider)
        self.assertEqual(provider.specialty_id, "research")

    def test_unregistered_specialty_returns_none_never_a_fabricated_provider(self) -> None:
        self.assertIsNone(get_provider("code-intelligence"))
        self.assertIsNone(get_provider("design-studio"))

    def test_supported_specialty_ids_lists_only_research(self) -> None:
        self.assertEqual(supported_specialty_ids(), ("research",))


class TestCapabilityMatrix(unittest.TestCase):
    """Lettre D du §21 — matrice déclarative SUPPORTED/UNSUPPORTED."""

    def test_research_declares_all_seven_kinds_supported(self) -> None:
        provider = ResearchTaskProvider()
        matrix = provider.capability_matrix()
        for kind in TaskKind:
            self.assertTrue(matrix.get(kind, False), f"{kind} devrait être supporté par research")

    def test_supports_reflects_the_matrix(self) -> None:
        provider = ResearchTaskProvider()
        self.assertTrue(provider.supports(TaskKind.EDGE_CASE))


class _NothingSupportedProvider(ResearchTaskProvider):
    """Double de test — déclare TOUT `UNSUPPORTED`, pour prouver que le
    moteur respecte réellement la matrice (§5) plutôt que de la contourner."""

    def capability_matrix(self) -> dict[TaskKind, bool]:
        return {kind: False for kind in TaskKind}


class TestUnsupportedCapability(unittest.TestCase):
    """Lettre E du §21 — une capacité UNSUPPORTED ne produit JAMAIS de
    contenu, quel que soit le moteur qui la sollicite."""

    def test_provider_create_raises_for_unsupported_kind(self) -> None:
        provider = _NothingSupportedProvider()
        with self.assertRaises(NotImplementedYet):
            provider.create(kind=TaskKind.NORMAL)

    def test_variant_engine_raises_for_unsupported_transformation(self) -> None:
        provider = _NothingSupportedProvider()
        with self.assertRaises(NotImplementedYet):
            provider.create_variant(parent_task=a_task(), transformation=TaskKind.EDGE_CASE, seed=1)

    def test_normal_and_targeted_weakness_are_never_valid_variant_transformations(self) -> None:
        provider = ResearchTaskProvider()
        with self.assertRaises(ValueError):
            provider.create_variant(parent_task=a_task(), transformation=TaskKind.NORMAL, seed=1)


class TestDeterministicGeneration(unittest.TestCase):
    """Lettre F du §21 — la génération reste 100% déterministe, jamais un
    contenu inventé, quel que soit le chemin d'accès (direct ou via le
    Universal Task Engine)."""

    def test_edge_case_kinds_all_produce_a_real_distinct_command(self) -> None:
        provider = ResearchTaskProvider()
        limits = a_limits()
        commands = {
            kind: provider.create(kind=TaskKind.EDGE_CASE, edge_case_kind=kind, limits=limits)
            for kind in EdgeCaseKind
        }
        self.assertEqual(len({spec.command for spec in commands.values()}), len(EdgeCaseKind))

    def test_bug_farm_reproduces_the_exact_base_command(self) -> None:
        provider = ResearchTaskProvider()
        base = a_task(command=("python3", "-c", "print('exact')"))
        generated = provider.create(kind=TaskKind.BUG_FARM, base_task=base)
        self.assertEqual(generated.command, base.command)
        self.assertEqual(generated.dataset_ref, f"bug_farm:{base.id}")

    def test_security_kind_routes_to_a_recognized_security_boundary_probe(self) -> None:
        provider = ResearchTaskProvider()
        generated = provider.create(kind=TaskKind.SECURITY)
        self.assertIn(
            generated.dataset_ref,
            {f"adversarial:{AdversarialKind.NETWORK_BOUNDARY_PROBE.value}",
             f"adversarial:{AdversarialKind.FILESYSTEM_BOUNDARY_PROBE.value}"},
        )

    def test_generalization_kind_produces_the_alternate_probe_by_default(self) -> None:
        provider = ResearchTaskProvider()
        generated = provider.create(kind=TaskKind.GENERALIZATION)
        self.assertEqual(generated.dataset_ref, f"generalization_variant:{GeneralizationVariant.ALTERNATE_PROBE.value}")


class TestDeterministicSeed(unittest.TestCase):
    """Lettres G/U/V/W du §21 — le moteur de variantes est une fonction
    PURE de `(parent_task, transformation, seed)`."""

    def test_same_seed_produces_the_same_task(self) -> None:
        engine = VariantEngine()
        base = a_task()
        limits = a_limits()
        request = VariantRequest(parent_task=base, transformation=TaskKind.EDGE_CASE, seed=7)
        first = engine.generate(request, limits=limits)
        second = engine.generate(request, limits=limits)
        self.assertEqual(first.command, second.command)
        self.assertEqual(first.provenance.seed, 7)
        self.assertEqual(first.provenance.parent_task_id, base.id)

    def test_different_seed_yields_a_valid_different_variant(self) -> None:
        engine = VariantEngine()
        base = a_task()
        limits = a_limits()
        outputs = {
            seed: engine.generate(
                VariantRequest(parent_task=base, transformation=TaskKind.ADVERSARIAL, seed=seed), limits=limits,
            ).command
            for seed in range(len(AdversarialKind))
        }
        self.assertEqual(len(set(outputs.values())), len(AdversarialKind))

    def test_repeated_generation_is_idempotent_in_content(self) -> None:
        """Lettre U — rappeler la MÊME requête à répétition ne produit
        jamais un contenu différent (aucune horloge/aléa caché)."""
        engine = VariantEngine()
        base = a_task()
        request = VariantRequest(parent_task=base, transformation=TaskKind.GENERALIZATION, seed=1)
        results = {engine.generate(request).command for _ in range(5)}
        self.assertEqual(len(results), 1)

    def test_regression_and_bug_farm_variants_always_reproduce_identically_regardless_of_seed(self) -> None:
        engine = VariantEngine()
        base = a_task()
        outputs = {
            seed: engine.generate(VariantRequest(parent_task=base, transformation=TaskKind.REGRESSION, seed=seed)).command
            for seed in range(5)
        }
        self.assertEqual(len(set(outputs.values())), 1)


class TestPolicyLimitsRespected(unittest.TestCase):
    """Lettre X du §21 — un adversarial/edge case ne demande JAMAIS plus
    que la politique administrateur RÉELLEMENT résolue."""

    def test_timeout_pressure_never_reaches_the_resolved_timeout(self) -> None:
        provider = ResearchTaskProvider()
        limits = a_limits(timeout_s=20.0)
        generated = provider.create(
            kind=TaskKind.ADVERSARIAL, adversarial_kind=AdversarialKind.TIMEOUT_PRESSURE, limits=limits,
        )
        script = generated.command[-1]
        self.assertIn("time.sleep(", script)
        # Extrait le nombre passé à `time.sleep` et vérifie qu'il reste
        # strictement sous `timeout_s` — jamais un dépassement de policy.
        sleep_value = float(script.split("time.sleep(")[1].split(")")[0])
        self.assertLess(sleep_value, limits.timeout_s)

    def test_resource_pressure_never_reaches_the_resolved_memory(self) -> None:
        provider = ResearchTaskProvider()
        limits = a_limits(memory_mb=100)
        generated = provider.create(
            kind=TaskKind.ADVERSARIAL, adversarial_kind=AdversarialKind.RESOURCE_PRESSURE, limits=limits,
        )
        script = generated.command[-1]
        allocated_bytes = int(script.split("bytearray(")[1].split(")")[0])
        self.assertLess(allocated_bytes, limits.memory_mb * 1024 * 1024)


class TestDifficultyModel(unittest.TestCase):
    """Lettre O du §21 — difficulté structurée, jamais un niveau arbitraire."""

    def test_comparisons_are_pure_functions_of_the_underlying_int(self) -> None:
        self.assertTrue(harder_than(Difficulty.EXPERT, Difficulty.EASY))
        self.assertTrue(easier_than(Difficulty.EASY, Difficulty.HARD))
        self.assertFalse(harder_than(Difficulty.MEDIUM, Difficulty.MEDIUM))

    def test_from_int_rejects_out_of_bounds_values(self) -> None:
        with self.assertRaises(ValueError):
            from_int(0)
        with self.assertRaises(ValueError):
            from_int(5)

    def test_clamp_never_exceeds_the_policy_ceiling(self) -> None:
        clamped = clamp_to_ceiling(Difficulty.EXPERT, max_allowed=Difficulty.MEDIUM)
        self.assertEqual(clamped, Difficulty.MEDIUM)
        unclamped = clamp_to_ceiling(Difficulty.EASY, max_allowed=Difficulty.HARD)
        self.assertEqual(unclamped, Difficulty.EASY)


class TestProvenanceAndConstraintsAreHonest(unittest.TestCase):
    def test_provenance_defaults_are_all_none_never_fabricated(self) -> None:
        provenance = TaskProvenance()
        self.assertIsNone(provenance.weakness_id)
        self.assertIsNone(provenance.seed)
        self.assertIsNone(provenance.generator_version)

    def test_constraints_default_to_no_limit(self) -> None:
        constraints = TaskConstraints()
        self.assertIsNone(constraints.limits)
        self.assertIsNone(constraints.max_difficulty)
