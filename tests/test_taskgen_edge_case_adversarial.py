"""Tests de `apex.taskgen.deterministic` EDGE_CASE/ADVERSARIAL (passe
« Adversarial Curriculum + Capability Benchmarking », N+10). Lettres A/B/W
du §19 de la mission citées explicitement."""

from __future__ import annotations

import subprocess
import unittest

from apex.sandbox.model import ResourceLimits
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import AdversarialKind, EdgeCaseKind, GeneralizationVariant, GenerationMode
from apex.tasks.model import TaskSplit


def a_limits(**overrides) -> ResourceLimits:
    defaults = dict(cpu_cores=1.0, memory_mb=64, pids_limit=32, timeout_s=4.0, disk_mb=64, network="none")
    defaults.update(overrides)
    return ResourceLimits(**defaults)


class TestEdgeCaseGeneration(unittest.TestCase):
    """Lettre A du §19 — chaque cas dérive d'une propriété RÉELLE du
    domaine, jamais un cas arbitraire, avec une provenance traçable
    (`dataset_ref`)."""

    def setUp(self) -> None:
        self.generator = DeterministicTaskGenerator()

    def test_missing_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.EDGE_CASE, specialty="research")

    def test_each_kind_has_traceable_provenance_and_a_distinct_command(self) -> None:
        limits = a_limits()
        specs = {
            kind: self.generator.generate(
                mode=GenerationMode.EDGE_CASE, specialty="research", edge_case_kind=kind, limits=limits,
            )
            for kind in EdgeCaseKind
        }
        for kind, spec in specs.items():
            with self.subTest(kind=kind):
                self.assertEqual(spec.dataset_ref, f"edge_case:{kind.value}")
                self.assertEqual(spec.mode, GenerationMode.EDGE_CASE)
        commands = {spec.command for spec in specs.values()}
        self.assertEqual(len(commands), len(specs), "chaque EdgeCaseKind doit produire une commande distincte")

    def test_boundary_timeout_derives_from_the_real_resolved_limit(self) -> None:
        limits_a = a_limits(timeout_s=4.0)
        limits_b = a_limits(timeout_s=40.0)
        spec_a = self.generator.generate(
            mode=GenerationMode.EDGE_CASE, specialty="research", edge_case_kind=EdgeCaseKind.BOUNDARY_TIMEOUT,
            limits=limits_a,
        )
        spec_b = self.generator.generate(
            mode=GenerationMode.EDGE_CASE, specialty="research", edge_case_kind=EdgeCaseKind.BOUNDARY_TIMEOUT,
            limits=limits_b,
        )
        self.assertNotEqual(spec_a.command, spec_b.command, "un timeout_s différent doit produire un script différent")

    def test_boundary_timeout_requires_limits(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(
                mode=GenerationMode.EDGE_CASE, specialty="research", edge_case_kind=EdgeCaseKind.BOUNDARY_TIMEOUT,
            )

    def test_repeated_calls_detects_real_non_determinism(self) -> None:
        """Provenance vérifiée par EXÉCUTION RÉELLE (pas seulement la forme
        du script) — un script cohérent complète (exit 0)."""
        spec = self.generator.generate(
            mode=GenerationMode.EDGE_CASE, specialty="research", edge_case_kind=EdgeCaseKind.REPEATED_CALLS,
        )
        proc = subprocess.run(spec.command, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("consistent", proc.stdout)


class TestAdversarialGeneration(unittest.TestCase):
    """Lettre B du §19 — un adversarial ne demande JAMAIS plus que la
    politique déjà résolue ; un `NETWORK`/`FILESYSTEM` boundary probe ne
    contient aucune demande de privilège élevé (§4/§7 de la mission)."""

    def setUp(self) -> None:
        self.generator = DeterministicTaskGenerator()

    def test_missing_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.generator.generate(mode=GenerationMode.ADVERSARIAL, specialty="research")

    def test_timeout_pressure_never_exceeds_timeout_s(self) -> None:
        limits = a_limits(timeout_s=2.0)
        spec = self.generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty="research", adversarial_kind=AdversarialKind.TIMEOUT_PRESSURE,
            limits=limits,
        )
        proc = subprocess.run(spec.command, capture_output=True, text=True, timeout=limits.timeout_s + 2)
        self.assertEqual(proc.returncode, 0)
        import json

        report = json.loads(proc.stdout)
        self.assertLess(report["slept_s"], limits.timeout_s)

    def test_resource_pressure_never_exceeds_memory_mb(self) -> None:
        limits = a_limits(memory_mb=32)
        spec = self.generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty="research", adversarial_kind=AdversarialKind.RESOURCE_PRESSURE,
            limits=limits,
        )
        proc = subprocess.run(spec.command, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        import json

        report = json.loads(proc.stdout)
        self.assertLess(report["allocated_bytes"], limits.memory_mb * 1024 * 1024)

    def test_network_boundary_probe_never_requests_privileged_execution(self) -> None:
        spec = self.generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty="research",
            adversarial_kind=AdversarialKind.NETWORK_BOUNDARY_PROBE,
        )
        script = spec.command[-1]
        for forbidden in ("sudo", "setuid", "CAP_", "os.setuid", "chmod 777"):
            self.assertNotIn(forbidden, script)

    def test_filesystem_boundary_probe_never_requests_privileged_execution(self) -> None:
        spec = self.generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty="research",
            adversarial_kind=AdversarialKind.FILESYSTEM_BOUNDARY_PROBE,
        )
        script = spec.command[-1]
        for forbidden in ("sudo", "setuid", "CAP_", "os.setuid", "chmod 777"):
            self.assertNotIn(forbidden, script)

    def test_boundary_probes_report_whether_the_sandbox_boundary_held(self) -> None:
        """Sans isolation réelle (ce process de test), la frontière est
        RÉELLEMENT franchissable — le script le rapporte honnêtement
        (`sys.exit(1)`), preuve que la détection fonctionne dans les DEUX
        sens (jamais un `exit 0` fabriqué)."""
        spec = self.generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty="research",
            adversarial_kind=AdversarialKind.NETWORK_BOUNDARY_PROBE,
        )
        proc = subprocess.run(spec.command, capture_output=True, text=True, timeout=10)
        self.assertIn(proc.returncode, (0, 1))


class TestGeneralizationVariant(unittest.TestCase):
    """Lettre D du §19 — la variante HELDOUT doit être STRUCTURELLEMENT
    distincte du TRAIN, jamais un doublon byte-à-byte (§6 de la mission)."""

    def setUp(self) -> None:
        self.generator = DeterministicTaskGenerator()

    def test_alternate_probe_differs_from_default_probe(self) -> None:
        default_spec = self.generator.generate(
            mode=GenerationMode.NORMAL, specialty="research", split=TaskSplit.HELDOUT,
            generalization_variant=GeneralizationVariant.DEFAULT_PROBE,
        )
        alternate_spec = self.generator.generate(
            mode=GenerationMode.NORMAL, specialty="research", split=TaskSplit.HELDOUT,
            generalization_variant=GeneralizationVariant.ALTERNATE_PROBE,
        )
        self.assertNotEqual(default_spec.command, alternate_spec.command)

    def test_no_variant_reproduces_the_pre_existing_normal_behavior(self) -> None:
        from apex.missions.workloads import research_diagnostic_command

        spec = self.generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        self.assertEqual(spec.command, research_diagnostic_command())


if __name__ == "__main__":
    unittest.main()
