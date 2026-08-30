"""Tests du modèle de domaine du Sandbox — dataclasses pures."""

from __future__ import annotations

import unittest

from apex.config.schema import SandboxDefaults
from apex.sandbox.model import (
    ExecutionStatus,
    IsolationLevel,
    ResourceLimits,
    SandboxCapabilityReport,
    SandboxSpec,
)


class TestIsolationLevel(unittest.TestCase):
    def test_none_and_rlimit_only_are_not_real_isolation(self) -> None:
        self.assertFalse(IsolationLevel.NONE.is_real_isolation)
        self.assertFalse(IsolationLevel.RLIMIT_ONLY.is_real_isolation)

    def test_namespaced_containerized_hardened_are_real_isolation(self) -> None:
        self.assertTrue(IsolationLevel.NAMESPACED.is_real_isolation)
        self.assertTrue(IsolationLevel.CONTAINERIZED.is_real_isolation)
        self.assertTrue(IsolationLevel.HARDENED.is_real_isolation)


class TestResourceLimitsFromConfig(unittest.TestCase):
    def test_never_reads_current_machine_hardware(self) -> None:
        """Correction architecturale de la session : aucune limite ne doit
        dériver du matériel détecté sur la machine courante."""
        defaults = SandboxDefaults.parse(
            {"cpu_limit": 3.5, "memory_mb": 2048, "pids_limit": 64, "timeout_s": 120}
        )
        limits = ResourceLimits.from_config(defaults)
        self.assertEqual(limits.cpu_cores, 3.5)
        self.assertEqual(limits.memory_mb, 2048)
        self.assertEqual(limits.pids_limit, 64)
        self.assertEqual(limits.timeout_s, 120.0)

    def test_disk_mb_is_overridable(self) -> None:
        defaults = SandboxDefaults.parse({})
        limits = ResourceLimits.from_config(defaults, disk_mb=256)
        self.assertEqual(limits.disk_mb, 256)


class TestSandboxCapabilityReport(unittest.TestCase):
    def _report(self, level: IsolationLevel) -> SandboxCapabilityReport:
        return SandboxCapabilityReport(
            level=level, namespaces_available=True, cgroups_available=True,
            privilege_drop_available=True, network_isolation_available=True,
            docker_daemon_reachable=False, seccomp_available=False, detail="test",
        )

    def test_is_available_requires_real_isolation(self) -> None:
        self.assertTrue(self._report(IsolationLevel.NAMESPACED).is_available)
        self.assertFalse(self._report(IsolationLevel.RLIMIT_ONLY).is_available)
        self.assertFalse(self._report(IsolationLevel.NONE).is_available)


class TestSandboxSpec(unittest.TestCase):
    def test_command_is_a_tuple_not_a_closure(self) -> None:
        """Une vraie isolation OS exige un argv exécutable, jamais un objet
        Python marshalé à travers la frontière de privilège."""
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        spec = SandboxSpec(mission_id="m", clone_id="c", command=("python3", "-c", "pass"), limits=limits)
        self.assertIsInstance(spec.command, tuple)
        self.assertTrue(all(isinstance(part, str) for part in spec.command))


class TestExecutionStatusVocabulary(unittest.TestCase):
    def test_no_silent_overrun_states_exist(self) -> None:
        """§5 de la consigne étape 7 : un dépassement de ressource doit
        toujours produire un état explicite, jamais un SUCCESS masqué."""
        required = {
            "success", "failed", "timeout", "oom_killed", "resource_limit_exceeded",
            "process_limit_exceeded", "disk_limit_exceeded", "network_denied", "permission_denied",
            # étape 8 : annulation explicite (Scheduler), distincte d'un
            # dépassement de ressource même si le signal noyau coïncide (9).
            "cancelled",
        }
        actual = {s.value for s in ExecutionStatus}
        self.assertEqual(actual, required)


if __name__ == "__main__":
    unittest.main()
