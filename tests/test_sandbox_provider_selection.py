"""Tests de la sélection explicite de backend — `apex.sandbox.provider.
select_sandbox_provider`/`get_default_provider` (mission N+25).

Contrat testé : NAMESPACED en priorité s'il est réellement disponible ;
sinon CONTAINERIZED seulement si Docker est réellement joignable ET si ses
garanties de sécurité sont vérifiées empiriquement ; sinon un refus observable
(`IsolationUnavailable` levée par `execute()`), jamais un choix opaque ou un
backend choisi au seul motif qu'un binaire est présent sur le PATH.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from apex.errors import IsolationUnavailable
from apex.sandbox.container import ContainerSandbox
from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
from apex.sandbox.model import IsolationLevel, ResourceLimits, SandboxCapabilityReport, SandboxSpec
from apex.sandbox.provider import get_default_provider, select_sandbox_provider


def _report(level: IsolationLevel, **overrides) -> SandboxCapabilityReport:
    defaults = dict(
        namespaces_available=False, cgroups_available=False, privilege_drop_available=False,
        network_isolation_available=False, docker_daemon_reachable=False, seccomp_available=False,
        detail="test",
    )
    defaults.update(overrides)
    return SandboxCapabilityReport(level=level, **defaults)


class TestSelectionPrefersNamespacedWhenAvailable(unittest.TestCase):
    def test_namespaced_is_chosen_without_ever_probing_docker(self) -> None:
        ns_report = _report(IsolationLevel.NAMESPACED)
        with patch("apex.sandbox.container.detect_container_capabilities") as probe:
            selection = select_sandbox_provider(namespace_report=ns_report)
        probe.assert_not_called()
        self.assertEqual(selection.provider_name, "LinuxNamespaceSandbox")
        self.assertIsInstance(selection.provider, LinuxNamespaceSandbox)
        self.assertIs(selection.report.level, IsolationLevel.NAMESPACED)
        self.assertIn("NAMESPACED", selection.reason.upper())


class TestSelectionFallsBackToContainerWhenNamespacesUnavailable(unittest.TestCase):
    def test_container_is_chosen_when_it_satisfies_guarantees(self) -> None:
        ns_report = _report(IsolationLevel.RLIMIT_ONLY, docker_daemon_reachable=True)
        container_report = _report(
            IsolationLevel.CONTAINERIZED, docker_daemon_reachable=True,
            namespaces_available=True, privilege_drop_available=True, network_isolation_available=True,
        )
        with patch("apex.sandbox.container.detect_container_capabilities", return_value=container_report):
            selection = select_sandbox_provider(namespace_report=ns_report, container_image="python:3.12-slim")
        self.assertEqual(selection.provider_name, "ContainerSandbox")
        self.assertIsInstance(selection.provider, ContainerSandbox)
        self.assertIs(selection.report.level, IsolationLevel.CONTAINERIZED)
        self.assertIn("Docker", selection.reason)

    def test_container_is_never_chosen_merely_because_docker_daemon_is_reachable(self) -> None:
        """Le coeur de la consigne : docker joignable ne suffit pas si la
        sonde de sécurité réelle échoue (ex. cap-drop/pids-limit refusés,
        réseau non coupé)."""
        ns_report = _report(IsolationLevel.RLIMIT_ONLY, docker_daemon_reachable=True)
        container_report = _report(IsolationLevel.NONE, docker_daemon_reachable=True)  # sonde échouée
        with patch("apex.sandbox.container.detect_container_capabilities", return_value=container_report):
            selection = select_sandbox_provider(namespace_report=ns_report)
        self.assertEqual(selection.provider_name, "LinuxNamespaceSandbox")
        self.assertFalse(selection.report.is_available)


class TestSelectionRefusesHonestlyWhenNothingAvailable(unittest.TestCase):
    def test_refusal_is_observable_and_execute_raises(self) -> None:
        ns_report = _report(IsolationLevel.RLIMIT_ONLY, docker_daemon_reachable=False)
        container_report = _report(IsolationLevel.NONE, docker_daemon_reachable=False)
        with patch("apex.sandbox.container.detect_container_capabilities", return_value=container_report):
            selection = select_sandbox_provider(namespace_report=ns_report)
        self.assertEqual(selection.provider_name, "LinuxNamespaceSandbox")
        self.assertIn("aucun backend d'isolation", selection.reason)
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        spec = SandboxSpec(mission_id="m", clone_id="c", command=("true",), limits=limits)
        with self.assertRaises(IsolationUnavailable):
            selection.provider.execute(spec)


class TestSelectionReportIsAlreadyCachedOnReturnedProvider(unittest.TestCase):
    def test_returned_provider_does_not_re_probe_on_detect(self) -> None:
        """`select_sandbox_provider` sonde une fois ; le `.detect()` du
        backend retourné doit relire le rapport déjà calculé, jamais sonder
        une seconde fois (coût, mais surtout cohérence : deux appels
        pourraient sinon donner des résultats différents pour un même choix)."""
        ns_report = _report(IsolationLevel.RLIMIT_ONLY, docker_daemon_reachable=True)
        container_report = _report(
            IsolationLevel.CONTAINERIZED, docker_daemon_reachable=True,
            namespaces_available=True, privilege_drop_available=True, network_isolation_available=True,
        )
        with patch(
            "apex.sandbox.container.detect_container_capabilities", return_value=container_report,
        ) as probe:
            selection = select_sandbox_provider(namespace_report=ns_report)
            selection.provider.detect()
            selection.provider.detect()
        self.assertEqual(probe.call_count, 1)


class TestGetDefaultProviderDelegatesToSelection(unittest.TestCase):
    def test_returns_the_selected_provider_instance_type(self) -> None:
        provider = get_default_provider()
        self.assertIsInstance(provider, LinuxNamespaceSandbox | ContainerSandbox)


if __name__ == "__main__":
    unittest.main()
