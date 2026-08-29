"""Tests des corrections K1 et K2.

K1 — GATE-11 rapportait « espace libre indetermine » sur une machine neuve,
parce que `paths.data_root` n'existe pas encore et que `shutil.disk_usage`
leve sur un chemin absent. Faux DEGRADED, reproduit sur la machine #1.

K2 — `docker-compose.yml` declarait 9,5 Go de limites memoire pour une machine
exposant 6,7 Go a WSL2, sans qu'aucun controle ne confronte les deux.

Les tests couvrent les cas exiges par l'autorisation d'implementation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apex.config.loader import parse_config
from apex.gate.checks import GateContext, gate_11_disk, gate_21_compose_memory
from apex.gate.compose import analyse_compose_file, parse_memory
from apex.gate.model import GateStatus
from apex.util.probe import FakeProbe, SystemProbe
from tests.test_config import config as base_config
from tests.test_doctor import healthy_probe

DATA_ROOT = "/home/apex/data"


def context(probe: FakeProbe, *, project_root: str = ".", overrides: dict | None = None):
    document = base_config()
    if overrides:
        document.update(overrides)
    return GateContext(
        probe=probe, config=parse_config(document), timeout_s=1.0, project_root=project_root
    )


# ---------------------------------------------------------------------------
# K1 — mesure d'espace disque
# ---------------------------------------------------------------------------


class TestK1DiskProbe(unittest.TestCase):
    def test_target_path_exists_and_is_measured(self) -> None:
        probe = healthy_probe()
        probe.paths.add(DATA_ROOT)
        probe.free_gb[DATA_ROOT] = 500.0
        result = probe.disk_probe(DATA_ROOT)
        self.assertTrue(result.requested_exists)
        self.assertFalse(result.fell_back_to_ancestor)
        self.assertEqual(result.measured_path, DATA_ROOT)
        self.assertEqual(result.free_gb, 500.0)

    def test_target_missing_but_ancestor_measured(self) -> None:
        """Le cas exact de la machine #1 : data_root pas encore cree."""
        probe = healthy_probe()
        probe.free_gb.clear()
        probe.free_gb["/home"] = 400.0
        result = probe.disk_probe(DATA_ROOT)
        self.assertFalse(result.requested_exists)
        self.assertTrue(result.fell_back_to_ancestor)
        self.assertEqual(result.measured_path, "/home")
        self.assertEqual(result.free_gb, 400.0)

    def test_falls_back_to_root_when_needed(self) -> None:
        probe = healthy_probe()
        probe.free_gb.clear()
        probe.free_gb["/"] = 120.0
        result = probe.disk_probe("/a/b/c/d")
        self.assertEqual(result.measured_path, "/")
        self.assertTrue(result.measured)

    def test_system_error_is_not_confused_with_missing_path(self) -> None:
        probe = healthy_probe()
        probe.free_gb.clear()
        probe.disk_errors["/home"] = "PermissionError: acces refuse"
        result = probe.disk_probe(DATA_ROOT)
        self.assertFalse(result.measured)
        self.assertIn("PermissionError", result.error or "")

    def test_no_measurable_ancestor(self) -> None:
        result = FakeProbe().disk_probe(DATA_ROOT)
        self.assertFalse(result.measured)
        self.assertEqual(result.error, "aucun ancetre existant")

    def test_probe_creates_nothing(self) -> None:
        """La sonde reste en lecture seule : aucun repertoire cree."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pas" / "encore" / "cree"
            result = SystemProbe().disk_probe(str(target))
            self.assertFalse(target.exists())
            self.assertTrue(result.measured)
            self.assertTrue(result.fell_back_to_ancestor)

    def test_disk_free_gb_still_works(self) -> None:
        """Non-regression : l'ancienne API delegue a la nouvelle."""
        probe = healthy_probe()
        probe.free_gb.clear()
        probe.free_gb["/home"] = 42.0
        self.assertEqual(probe.disk_free_gb(DATA_ROOT), 42.0)


class TestK1Gate11(unittest.TestCase):
    def _ctx(self, **kw):
        probe = healthy_probe()
        probe.free_gb.clear()
        probe.free_gb.update(kw.pop("free_gb", {}))
        probe.disk_errors.update(kw.pop("disk_errors", {}))
        for path in kw.pop("existing", []):
            probe.paths.add(path)
        document = base_config()
        document["paths"] = {
            "data_root": DATA_ROOT, "workspaces": f"{DATA_ROOT}/w",
            "artifacts": f"{DATA_ROOT}/a", "logs": f"{DATA_ROOT}/l",
        }
        return GateContext(probe=probe, config=parse_config(document), timeout_s=1.0)

    def test_missing_path_with_ample_space_passes(self) -> None:
        """Le faux DEGRADED signale sur la machine #1 est corrige."""
        outcome = gate_11_disk(self._ctx(free_gb={"/home": 400.0}))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertFalse(outcome.facts["path_exists"])
        self.assertTrue(outcome.facts["measured_on_ancestor"])
        self.assertEqual(outcome.facts["path_measured"], "/home")
        self.assertIn("n'existe pas encore", outcome.message)

    def test_existing_path_with_ample_space_passes(self) -> None:
        outcome = gate_11_disk(self._ctx(free_gb={DATA_ROOT: 400.0}, existing=[DATA_ROOT]))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertFalse(outcome.facts["measured_on_ancestor"])

    def test_genuinely_insufficient_space_still_degrades(self) -> None:
        """Le correctif ne doit pas masquer un vrai manque de place."""
        outcome = gate_11_disk(self._ctx(free_gb={"/home": 3.0}))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertEqual(outcome.facts["disk_free_gb"], 3.0)

    def test_system_error_degrades_with_the_error_visible(self) -> None:
        outcome = gate_11_disk(self._ctx(disk_errors={"/home": "PermissionError: refuse"}))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertIn("PermissionError", outcome.facts["disk_error"])
        self.assertIn("Erreur systeme", outcome.remediation)

    def test_nothing_measurable_is_unknown_not_degraded(self) -> None:
        ctx = GateContext(probe=FakeProbe(), config=parse_config(base_config()), timeout_s=1.0)
        outcome = gate_11_disk(ctx)
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertEqual(outcome.unknown_reason, "unmeasurable")

    def test_measured_path_is_always_reported(self) -> None:
        outcome = gate_11_disk(self._ctx(free_gb={"/home": 400.0}))
        self.assertIn("path_measured", outcome.facts)
        self.assertIn("path_requested", outcome.facts)


# ---------------------------------------------------------------------------
# K2 — analyse Compose
# ---------------------------------------------------------------------------


class TestParseMemory(unittest.TestCase):
    def test_units(self) -> None:
        gib = 1024**3
        self.assertEqual(parse_memory("6G"), 6 * gib)
        self.assertEqual(parse_memory("1.5G"), int(1.5 * gib))
        self.assertEqual(parse_memory("2Gi"), 2 * gib)
        self.assertEqual(parse_memory("512m"), 512 * 1024**2)
        self.assertEqual(parse_memory("512Mi"), 512 * 1024**2)
        self.assertEqual(parse_memory("1024k"), 1024 * 1024)

    def test_bare_integer_is_bytes(self) -> None:
        self.assertEqual(parse_memory(1073741824), 1073741824)
        self.assertEqual(parse_memory("1073741824"), 1073741824)

    def test_unparseable_returns_none_not_a_default(self) -> None:
        for value in ("abc", "", None, "12 zzz", True):
            self.assertIsNone(parse_memory(value), value)


class TestComposeAnalysis(unittest.TestCase):
    def _write(self, content: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "docker-compose.yml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_multiple_services_are_summed(self) -> None:
        analysis = analyse_compose_file(self._write("""
services:
  a: {image: x, mem_limit: 2G}
  b: {image: y, mem_limit: 1G}
  c: {image: z, mem_limit: 512m}
"""))
        self.assertAlmostEqual(analysis.total_limit_gb, 3.5, places=2)
        self.assertEqual(len(analysis.services), 3)

    def test_service_without_limit_is_listed(self) -> None:
        analysis = analyse_compose_file(self._write("""
services:
  bounded: {image: x, mem_limit: 1G}
  unbounded: {image: y}
"""))
        self.assertEqual(analysis.services_without_limit, ("unbounded",))

    def test_reservations_do_not_count_as_limits(self) -> None:
        """Une reservation ne plafonne rien : elle ne peut pas sur-allouer."""
        analysis = analyse_compose_file(self._write("""
services:
  a:
    image: x
    deploy:
      resources:
        reservations: {memory: 4G}
"""))
        self.assertEqual(analysis.total_limit_bytes, 0)
        self.assertAlmostEqual(analysis.total_reservation_gb, 4.0, places=2)
        self.assertEqual(analysis.services_without_limit, ("a",))

    def test_deploy_only_limits_are_flagged_for_v1(self) -> None:
        analysis = analyse_compose_file(self._write("""
services:
  a:
    image: x
    deploy:
      resources:
        limits: {memory: 2G}
"""))
        self.assertEqual(analysis.services_ignored_on_v1, ("a",))

    def test_short_form_is_enforced_everywhere(self) -> None:
        analysis = analyse_compose_file(self._write("""
services:
  a: {image: x, mem_limit: 2G}
"""))
        self.assertEqual(analysis.services_ignored_on_v1, ())

    def test_both_forms_take_the_lower_ceiling(self) -> None:
        analysis = analyse_compose_file(self._write("""
services:
  a:
    image: x
    mem_limit: 1G
    deploy:
      resources:
        limits: {memory: 4G}
"""))
        self.assertAlmostEqual(analysis.total_limit_gb, 1.0, places=2)

    def test_invalid_yaml_is_reported(self) -> None:
        analysis = analyse_compose_file(self._write("services: [oups\n  - :"))
        self.assertIsNotNone(analysis.error)

    def test_missing_file_is_reported(self) -> None:
        self.assertEqual(analyse_compose_file("/nonexistent/dc.yml").error, "fichier introuvable")

    def test_no_memory_section_at_all(self) -> None:
        analysis = analyse_compose_file(self._write("services:\n  a: {image: x}\n"))
        self.assertIsNone(analysis.error)
        self.assertEqual(analysis.total_limit_bytes, 0)

    def test_unreadable_memory_value_is_an_error_not_an_absence(self) -> None:
        analysis = analyse_compose_file(self._write("services:\n  a: {image: x, mem_limit: beaucoup}\n"))
        self.assertTrue(analysis.parse_errors)

    def test_env_substitution_is_applied(self) -> None:
        analysis = analyse_compose_file(
            self._write("services:\n  a: {image: x, mem_limit: '${APEX_PG_MEM:-2g}'}\n")
        )
        self.assertAlmostEqual(analysis.total_limit_gb, 2.0, places=2)


class TestK2Gate21(unittest.TestCase):
    def _project(self, compose: str) -> str:
        root = Path(tempfile.mkdtemp())
        (root / "docker-compose.yml").write_text(compose, encoding="utf-8")
        return str(root)

    def _probe(self, ram_gb: float, *, docker: bool = False, compose_v2: bool = False) -> FakeProbe:
        from apex.util.probe import CommandResult

        probe = healthy_probe()
        kb = int(ram_gb * 1024 * 1024)
        probe.files["/proc/meminfo"] = f"MemTotal: {kb} kB\nMemAvailable: {kb // 2} kB\n"
        if not docker:
            probe.programs.pop("docker", None)
        if compose_v2:
            probe.commands[("docker", "compose", "version", "--short")] = CommandResult(
                ("docker",), 0, "2.29.1\n", ""
            )
        return probe

    def test_overallocation_fails_and_names_the_consumers(self) -> None:
        """Le cas machine #1 : 9,5 Go declares pour 6,7 Go visibles."""
        root = self._project("""
services:
  postgres: {image: p, mem_limit: 6G}
  redis: {image: r, mem_limit: 1.5G}
  minio: {image: m, mem_limit: 2G}
""")
        outcome = gate_21_compose_memory(context(self._probe(6.7), project_root=root))
        self.assertIs(outcome.status, GateStatus.FAIL)
        self.assertGreater(outcome.facts["overcommit_ratio"], 1.0)
        self.assertIn("postgres", outcome.remediation)
        self.assertIn(2, outcome.blocks_phases)

    def test_sufficient_ram_with_docker_absent_is_unknown_not_pass(self) -> None:
        """Sans Docker, on ne peut pas affirmer que les limites seront appliquees."""
        root = self._project("services:\n  a: {image: x, mem_limit: 1G}\n")
        outcome = gate_21_compose_memory(context(self._probe(16.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertEqual(outcome.unknown_reason, "dependency_missing")
        self.assertIsNone(outcome.facts["limits_enforced"])

    def test_sufficient_ram_with_compose_v2_passes(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 1G}\n")
        probe = self._probe(16.0, docker=True, compose_v2=True)
        outcome = gate_21_compose_memory(context(probe, project_root=root))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertTrue(outcome.facts["limits_enforced"])
        self.assertIn("v2", outcome.facts["compose_runtime"])

    def test_service_without_limit_degrades(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 1G}\n  b: {image: y}\n")
        outcome = gate_21_compose_memory(context(self._probe(16.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertEqual(outcome.facts["services_without_limit"], ["b"])

    def test_deploy_only_limits_degrade_without_v2(self) -> None:
        root = self._project("""
services:
  a:
    image: x
    deploy:
      resources:
        limits: {memory: 1G}
""")
        outcome = gate_21_compose_memory(context(self._probe(16.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertEqual(outcome.facts["services_ignored_on_compose_v1"], ["a"])
        self.assertIn("SILENCIEUSEMENT", outcome.remediation)

    def test_tight_margin_degrades_before_failing(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 7G}\n")
        outcome = gate_21_compose_memory(context(self._probe(8.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertAlmostEqual(outcome.facts["overcommit_ratio"], 0.88, places=1)

    def test_invalid_yaml_fails(self) -> None:
        root = self._project("services: [oups\n  - :")
        outcome = gate_21_compose_memory(context(self._probe(16.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.FAIL)

    def test_missing_compose_file_is_not_applicable(self) -> None:
        root = str(Path(tempfile.mkdtemp()))
        outcome = gate_21_compose_memory(context(self._probe(16.0), project_root=root))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertEqual(outcome.unknown_reason, "not_applicable")

    def test_ram_unmeasurable_yields_unknown(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 1G}\n")
        probe = self._probe(16.0)
        probe.files.pop("/proc/meminfo")
        outcome = gate_21_compose_memory(context(probe, project_root=root))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertEqual(outcome.unknown_reason, "unmeasurable")

    def test_policy_thresholds_come_from_config(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 7G}\n")
        document = base_config()
        document["gate"] = {"compose_memory": {"warn_ratio": 0.95, "fail_ratio": 1.0}}
        ctx = GateContext(
            probe=self._probe(8.0), config=parse_config(document), timeout_s=1.0, project_root=root
        )
        outcome = gate_21_compose_memory(ctx)
        # Avec un seuil d'alerte a 0.95, un ratio de 0.88 ne declenche plus DEGRADED.
        self.assertIsNot(outcome.status, GateStatus.DEGRADED)

    def test_check_never_starts_a_container(self) -> None:
        root = self._project("services:\n  a: {image: x, mem_limit: 1G}\n")
        probe = self._probe(16.0, docker=True, compose_v2=True)
        gate_21_compose_memory(context(probe, project_root=root))
        started = [c for c in probe.calls if c[0] == "run" and " run " in f" {c[1]} "]
        self.assertEqual(started, [])


class TestRealComposeFile(unittest.TestCase):
    """Le fichier reel du depot doit rester analysable et borne."""

    def test_repository_compose_is_analysable(self) -> None:
        analysis = analyse_compose_file("docker-compose.yml")
        self.assertIsNone(analysis.error)
        self.assertEqual(analysis.parse_errors, ())
        self.assertEqual(analysis.services_without_limit, ())

    def test_repository_compose_declares_short_form_limits(self) -> None:
        """Non-regression K2 : les limites ne doivent pas disparaitre sur Compose V1."""
        analysis = analyse_compose_file("docker-compose.yml")
        self.assertEqual(analysis.services_ignored_on_v1, ())

    def test_repository_compose_fits_a_modest_machine(self) -> None:
        """Le total par defaut doit tenir dans la RAM visible d'une machine 8 Go."""
        analysis = analyse_compose_file("docker-compose.yml")
        self.assertLess(analysis.total_limit_gb, 6.0)


if __name__ == "__main__":
    unittest.main()
