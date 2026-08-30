"""Tests de détection empirique — apex.sandbox.detect.

Ces tests s'exécutent contre le VRAI noyau de la machine : ils ne mockent
aucun mécanisme, exactement comme le veut la consigne étape 7 ("privilégie
des tests d'intégration réels du noyau plutôt que des mocks"). Le niveau
attendu ici (NAMESPACED) est celui vérifié manuellement pour cette machine
au moment de l'audit — voir apex/sandbox/detect.py pour la garantie que ce
résultat n'est jamais extrapolé à une autre machine.
"""

from __future__ import annotations

import unittest

from apex.sandbox.detect import (
    _check_cgroups_v1_writable,
    _check_docker_daemon,
    _check_namespaces,
    _check_network_isolation,
    _check_privilege_drop,
    detect_capabilities,
)
from apex.sandbox.model import IsolationLevel


class TestIndividualChecks(unittest.TestCase):
    """Chaque vérification est testée isolément — si l'une d'elles change
    de comportement sur une machine différente, ce test le montre
    directement plutôt que de le masquer derrière le niveau agrégé."""

    def test_namespaces_check_is_real(self) -> None:
        ok, detail = _check_namespaces()
        self.assertTrue(ok, detail)
        self.assertIn("succès", detail)

    def test_cgroups_v1_check_writes_and_removes_a_real_directory(self) -> None:
        ok, detail, per_controller = _check_cgroups_v1_writable()
        self.assertTrue(ok, detail)
        self.assertEqual(per_controller, {"cpu": True, "memory": True, "pids": True})

    def test_privilege_drop_actually_changes_uid(self) -> None:
        ok, detail = _check_privilege_drop()
        self.assertTrue(ok, detail)
        self.assertIn("65534", detail)

    def test_network_isolation_actually_blocks_a_connection(self) -> None:
        ok, detail = _check_network_isolation()
        self.assertTrue(ok, detail)

    def test_docker_daemon_is_honestly_reported_unreachable(self) -> None:
        """Le CLI docker est présent sur cette machine, le démon ne l'est
        pas — ce test échouerait franchement si un jour un démon apparaît,
        ce qui est le comportement voulu (jamais une supposition figée)."""
        ok, detail = _check_docker_daemon()
        self.assertFalse(ok)
        self.assertTrue(detail)


class TestDetectCapabilities(unittest.TestCase):
    def test_this_machine_reaches_namespaced(self) -> None:
        report = detect_capabilities()
        self.assertIs(report.level, IsolationLevel.NAMESPACED)
        self.assertTrue(report.is_available)
        # Le detail doit explicitement DÉMENTIR ces deux qualificatifs, jamais
        # les affirmer sans négation — voir §1 de la consigne étape 7.
        detail = report.detail.lower()
        self.assertIn("pas container-grade", detail)
        self.assertNotIn("fully isolated", detail)

    def test_report_never_claims_seccomp_or_docker_on_this_machine(self) -> None:
        """Honnêteté : ni Docker ni un seccomp EXPLOITABLE par ce code ne
        sont disponibles ici — le rapport doit le dire, jamais optimiste par
        défaut. (libseccomp.so existe sur cette machine, mais sans binding
        Python il est inutilisable : « disponible » doit refléter ce qui est
        réellement exploitable, pas la présence d'un fichier sur le disque —
        bug trouvé et corrigé par ce test.)"""
        report = detect_capabilities()
        self.assertFalse(report.docker_daemon_reachable)
        self.assertFalse(report.seccomp_available)
        self.assertIn("non exploitable", report.facts["seccomp"])

    def test_facts_are_populated_for_every_check(self) -> None:
        report = detect_capabilities()
        for key in (
            "namespaces", "cgroups_v1", "privilege_drop",
            "network_isolation", "docker_daemon", "seccomp",
        ):
            with self.subTest(key=key):
                self.assertIn(key, report.facts)
                self.assertTrue(report.facts[key])


if __name__ == "__main__":
    unittest.main()
