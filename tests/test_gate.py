"""Tests du Hardware & Environment Gate.

Les tests les plus importants ici ne vérifient pas des valeurs mais des
**refus** : refus de conclure sans sonde réelle, refus de confondre inconnu et
absent, refus d'exécuter un conteneur sans consentement, refus de relâcher une
garantie de sécurité pour satisfaire un contrôle.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from apex.config.loader import parse_config
from apex.gate.checks import (
    GateContext,
    gate_07_gpu_from_wsl,
    gate_09_vram,
    gate_01_windows_host,
    gate_05_python,
    gate_12_ram,
    gate_14_virtualization,
    gate_17_container_exec,
    gate_18_gpu_in_container,
    gate_19_isolation_state,
    gate_20_runsc_gonogo,
)
from apex.gate.contract import Environment, EnvironmentUnknown, load_environment
from apex.gate.model import DEPENDENCIES, GateCheckResult, GateStatus
from apex.gate.report import EnvironmentReport, GateException
from apex.gate.runner import run_gate
from apex.util.probe import CommandResult, FakeProbe, SystemProbe
from tests.test_config import config as base_config
from tests.test_doctor import healthy_probe


def ctx(probe: FakeProbe, **kw) -> GateContext:
    return GateContext(probe=probe, config=parse_config(base_config()), timeout_s=1.0, **kw)


def make_report(*results: GateCheckResult, real: bool = True, **kw) -> EnvironmentReport:
    return EnvironmentReport(checks=results, probe_is_real=real, **kw)


def check(check_id: str, status: GateStatus, **facts) -> GateCheckResult:
    return GateCheckResult(check_id=check_id, title=check_id, status=status,
                           message="", facts=facts)


class TestSimulatedDegradation(unittest.TestCase):
    """Le statut SIMULATED est dérivé de la sonde, jamais déclaré."""

    def test_fake_probe_degrades_every_conclusive_status(self) -> None:
        report = make_report(
            check("GATE-05", GateStatus.PASS),
            check("GATE-06", GateStatus.FAIL),
            check("GATE-11", GateStatus.DEGRADED),
            real=False,
        )
        self.assertTrue(all(c.status is GateStatus.SIMULATED for c in report.effective_checks))
        self.assertIs(report.status, GateStatus.SIMULATED)

    def test_not_tested_is_preserved_under_simulation(self) -> None:
        report = make_report(check("GATE-15", GateStatus.UNKNOWN), real=False)
        self.assertIs(report.effective_checks[0].status, GateStatus.UNKNOWN)

    def test_simulated_report_never_authorizes_phase_two(self) -> None:
        report = make_report(check("GATE-05", GateStatus.PASS), real=False)
        self.assertFalse(report.phase_2_authorized)

    def test_simulated_results_are_flagged_for_target_validation(self) -> None:
        report = make_report(check("GATE-05", GateStatus.PASS), real=False)
        self.assertTrue(report.effective_checks[0].to_validate_on_target)

    def test_runner_detects_probe_provenance_itself(self) -> None:
        """La provenance est constatée, pas fournie par l'appelant."""
        report = run_gate(probe=healthy_probe(), exceptions_path="/nonexistent")
        self.assertFalse(report.probe_is_real)
        self.assertIs(report.status, GateStatus.SIMULATED)
        self.assertTrue(isinstance(SystemProbe(), SystemProbe))


class TestSecurityIsNeverRelaxed(unittest.TestCase):
    def test_interop_disabled_yields_not_tested_not_a_workaround(self) -> None:
        """GATE-01 devient impossible parce que le durcissement est correct."""
        probe = healthy_probe()  # interop désactivée dans /etc/wsl.conf
        outcome = gate_01_windows_host(ctx(probe))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertIsNone(outcome.facts["windows_version"])
        self.assertIn("ne réactive pas l'interop", outcome.remediation)

    def test_missing_cgroups_blocks_sandbox_phase(self) -> None:
        probe = healthy_probe()
        probe.paths.discard("/sys/fs/cgroup/cgroup.controllers")
        outcome = gate_14_virtualization(ctx(probe))
        self.assertIs(outcome.status, GateStatus.FAIL)
        self.assertIn(3, outcome.blocks_phases)

    def test_docker_only_isolation_disables_red_team(self) -> None:
        probe = healthy_probe()
        probe.commands[("docker", "info", "--format", "{{.Runtimes}}")] = CommandResult(
            ("docker",), 0, "map[runc:{}]\n", ""
        )
        outcome = gate_19_isolation_state(ctx(probe))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertFalse(outcome.facts["red_team_allowed"])
        self.assertTrue(outcome.facts["code_execution_allowed"])
        self.assertIn(21, outcome.blocks_phases)

    def test_no_isolation_at_all_blocks_code_execution(self) -> None:
        probe = healthy_probe()
        probe.paths.discard("/sys/fs/cgroup/cgroup.controllers")
        probe.commands[("docker", "info", "--format", "{{.Runtimes}}")] = CommandResult(
            ("docker",), 0, "map[runc:{}]\n", ""
        )
        outcome = gate_19_isolation_state(ctx(probe))
        self.assertIs(outcome.status, GateStatus.FAIL)
        self.assertFalse(outcome.facts["code_execution_allowed"])


class TestConsentRequiredForSideEffects(unittest.TestCase):
    def test_container_checks_default_to_not_tested(self) -> None:
        for func in (gate_17_container_exec, gate_18_gpu_in_container):
            outcome = func(ctx(healthy_probe()))
            self.assertIs(outcome.status, GateStatus.UNKNOWN, func.__name__)

    def test_no_container_is_started_without_consent(self) -> None:
        probe = healthy_probe()
        gate_17_container_exec(ctx(probe))
        started = [c for c in probe.calls if c[0] == "run" and "docker run" in c[1]]
        self.assertEqual(started, [])

    def test_runsc_declared_is_not_runsc_functional(self) -> None:
        """Le nuance qui compte : déclaré ≠ opérationnel."""
        outcome = gate_20_runsc_gonogo(ctx(healthy_probe()))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertTrue(outcome.facts["runsc_declared"])
        self.assertIsNone(outcome.facts["runsc_functional"])

    def test_runsc_absent_is_a_documented_no_go(self) -> None:
        probe = healthy_probe()
        probe.commands[("docker", "info", "--format", "{{.Runtimes}}")] = CommandResult(
            ("docker",), 0, "map[runc:{}]\n", ""
        )
        outcome = gate_20_runsc_gonogo(ctx(probe))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertFalse(outcome.facts["runsc_functional"])
        for expected in ("Pourquoi", "Installation", "Phase concernée", "Repli"):
            self.assertIn(expected, outcome.remediation)

    def test_container_exec_uses_hardened_profile(self) -> None:
        probe = healthy_probe()
        probe.commands[(
            "docker", "run", "--rm", "--network", "none", "--user", "65534:65534",
            "--read-only", "--cap-drop", "ALL", "--pids-limit", "64",
            "docker.io/library/alpine:3.20", "echo", "apex-gate-ok",
        )] = CommandResult(("docker",), 0, "apex-gate-ok\n", "")
        outcome = gate_17_container_exec(ctx(probe, allow_container_exec=True))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertTrue(outcome.facts["hardened_profile_ok"])


class TestCapabilitiesUnknownVsAbsent(unittest.TestCase):
    def test_untested_capability_is_null_not_false(self) -> None:
        report = make_report(check("GATE-18", GateStatus.UNKNOWN, gpu_in_container=None))
        self.assertIsNone(report.capabilities()["gpu_in_container"])

    def test_contract_refuses_to_answer_on_unknown(self) -> None:
        env = Environment({
            "measured_on_target_machine": True,
            "capabilities": {"gpu_in_container": None, "cgroups_v2": True},
        })
        self.assertTrue(env.capability("cgroups_v2"))
        with self.assertRaises(EnvironmentUnknown):
            env.capability("gpu_in_container")

    def test_contract_refuses_a_simulated_report(self) -> None:
        env = Environment({"measured_on_target_machine": False,
                           "capabilities": {"cgroups_v2": True}})
        with self.assertRaises(EnvironmentUnknown):
            env.capability("cgroups_v2")

    def test_missing_report_is_an_explicit_error(self) -> None:
        with self.assertRaises(EnvironmentUnknown):
            load_environment("/nonexistent/environment.json")


class TestExceptions(unittest.TestCase):
    def test_active_exception_unblocks_a_failing_check(self) -> None:
        exc = GateException("GATE-06", "motif", "rafael", date.today() + timedelta(days=30))
        report = make_report(check("GATE-06", GateStatus.FAIL), exceptions=(exc,))
        self.assertEqual(report.blocking, ())

    def test_expired_exception_stops_applying(self) -> None:
        """Une dérogation permanente n'est pas une dérogation."""
        exc = GateException("GATE-06", "motif", "rafael", date.today() - timedelta(days=1))
        report = make_report(check("GATE-06", GateStatus.FAIL), exceptions=(exc,))
        self.assertEqual(len(report.blocking), 1)
        self.assertEqual(len(report.expired_exceptions), 1)

    def test_exception_requires_expiry_date(self) -> None:
        from apex.errors import ConfigError

        with self.assertRaises(ConfigError):
            GateException.parse({"check_id": "GATE-06", "reason": "x", "approved_by": "y"})

    def test_exception_does_not_change_the_underlying_fact(self) -> None:
        """Excuser un contrôle ne modifie pas la capacité rapportée."""
        exc = GateException("GATE-19", "motif", "rafael", date.today() + timedelta(days=30))
        report = make_report(
            check("GATE-19", GateStatus.FAIL, red_team_allowed=False), exceptions=(exc,)
        )
        self.assertEqual(report.blocking, ())
        self.assertFalse(report.capabilities()["red_team_allowed"])


class TestPhaseTwoAuthorization(unittest.TestCase):
    def test_unresolved_checks_block_authorization(self) -> None:
        report = make_report(
            check("GATE-05", GateStatus.PASS), check("GATE-17", GateStatus.UNKNOWN)
        )
        self.assertFalse(report.phase_2_authorized)
        self.assertIs(report.status, GateStatus.UNKNOWN)

    def test_warnings_alone_authorize_phase_two(self) -> None:
        report = make_report(check("GATE-05", GateStatus.PASS), check("GATE-11", GateStatus.DEGRADED))
        self.assertTrue(report.phase_2_authorized)
        self.assertIs(report.status, GateStatus.DEGRADED)

    def test_excused_unresolved_check_authorizes(self) -> None:
        exc = GateException("GATE-20", "runsc no-go assumé", "rafael",
                            date.today() + timedelta(days=90))
        report = make_report(
            check("GATE-05", GateStatus.PASS), check("GATE-20", GateStatus.UNKNOWN),
            exceptions=(exc,),
        )
        self.assertTrue(report.phase_2_authorized)


class TestMachineReadableReport(unittest.TestCase):
    def test_schema_is_versioned_and_complete(self) -> None:
        report = make_report(check("GATE-05", GateStatus.PASS, python_version="3.12.3"))
        payload = report.to_dict()
        for key in ("schema_version", "fingerprint", "capabilities", "hardware",
                    "gate_status", "phase_2_authorized", "checks", "missing_dependencies"):
            self.assertIn(key, payload)
        self.assertEqual(payload["schema_version"], 1)

    def test_fingerprint_changes_with_hardware(self) -> None:
        a = make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "RTX 4090"}]))
        b = make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "RTX 3060"}]))
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_fingerprint_is_stable_across_runs(self) -> None:
        facts = {"gpus": [{"model": "RTX 4090"}], "cpu_threads": 12}
        self.assertEqual(
            make_report(check("GATE-08", GateStatus.PASS, **facts)).fingerprint(),
            make_report(check("GATE-08", GateStatus.PASS, **facts)).fingerprint(),
        )

    def test_report_roundtrips_through_the_contract(self) -> None:
        report = make_report(check("GATE-14", GateStatus.PASS, cgroups_v2=True))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "environment.json"
            report.write(path)
            env = load_environment(path)
            self.assertTrue(env.capability("cgroups_v2"))
            self.assertEqual(env.fingerprint, report.fingerprint())

    def test_report_contains_no_secrets(self) -> None:
        report = run_gate(probe=healthy_probe(), exceptions_path="/nonexistent")
        self.assertNotIn("PASSWORD", json.dumps(report.to_dict()).upper())

    def test_missing_dependencies_are_documented_on_all_four_axes(self) -> None:
        report = make_report(check("GATE-20", GateStatus.DEGRADED))
        entries = report.missing_dependencies()
        self.assertTrue(entries)
        for entry in entries:
            self.assertTrue(entry["why"])
            self.assertTrue(entry["install"])
            self.assertTrue(entry["fallback"])
            self.assertIsInstance(entry["required_from_phase"], int)


class TestDependencyCatalogue(unittest.TestCase):
    def test_every_dependency_documents_the_four_axes(self) -> None:
        for dep in DEPENDENCIES:
            self.assertTrue(dep.why, dep.name)
            self.assertTrue(dep.install, dep.name)
            self.assertTrue(dep.fallback, dep.name)
            self.assertGreaterEqual(dep.required_from_phase, 1, dep.name)

    def test_every_dependency_maps_to_a_real_check(self) -> None:
        from apex.gate.checks import ALL_GATE_CHECKS

        known = {check_id for check_id, _ in ALL_GATE_CHECKS}
        for dep in DEPENDENCIES:
            self.assertIn(dep.check_id, known, dep.name)


class TestGateCoverage(unittest.TestCase):
    def test_check_ids_are_contiguous(self) -> None:
        from apex.gate.checks import ALL_GATE_CHECKS

        self.assertEqual(len(ALL_GATE_CHECKS), 21)
        self.assertEqual(
            [c for c, _ in ALL_GATE_CHECKS],
            [f"GATE-{i:02d}" for i in range(1, 22)],
        )

    def test_gate_never_crashes(self) -> None:
        report = run_gate(probe=FakeProbe(), exceptions_path="/nonexistent")
        self.assertEqual(len(report.checks), 21)

    def test_python_check_is_conclusive_everywhere(self) -> None:
        outcome = gate_05_python(ctx(healthy_probe()))
        self.assertTrue(outcome.status.is_conclusive)


class TestUnknownIsAlwaysQualified(unittest.TestCase):
    """UNKNOWN sans motif est indistinguable d'un oubli."""

    def test_every_unknown_carries_a_reason(self) -> None:
        report = run_gate(probe=FakeProbe(), exceptions_path="/nonexistent")
        for item in report.checks:
            if item.status is GateStatus.UNKNOWN:
                self.assertIn(
                    item.unknown_reason,
                    {"not_authorized", "not_applicable", "unmeasurable"},
                    item.check_id,
                )

    def test_consent_gated_checks_say_not_authorized(self) -> None:
        for func in (gate_17_container_exec, gate_18_gpu_in_container):
            self.assertEqual(func(ctx(healthy_probe())).unknown_reason, "not_authorized")

    def test_security_blocked_check_says_unmeasurable(self) -> None:
        """GATE-01 est empêché par un durcissement voulu, pas par un manque."""
        self.assertEqual(gate_01_windows_host(ctx(healthy_probe())).unknown_reason, "unmeasurable")

    def test_reason_survives_simulated_degradation(self) -> None:
        report = run_gate(probe=FakeProbe(), exceptions_path="/nonexistent")
        unknowns = [c for c in report.effective_checks if c.status is GateStatus.UNKNOWN]
        self.assertTrue(unknowns)
        self.assertTrue(all(c.unknown_reason for c in unknowns))



class TestRendering(unittest.TestCase):
    """Régression : le renommage du vocabulaire avait laissé les anciennes
    étiquettes à l'affichage — le rapport texte annonçait NOT_TESTED alors que
    le JSON disait UNKNOWN. Deux vocabulaires pour un même fait est exactement
    ce que le renommage voulait supprimer."""

    def test_rendered_labels_match_the_enum(self) -> None:
        from apex.gate.runner import render_text

        report = run_gate(probe=FakeProbe(), exceptions_path="/nonexistent")
        text = render_text(report)
        for legacy in ("NOT_TESTED", "WARNING"):
            self.assertNotIn(legacy, text)
        self.assertIn("SIMULATED", text)

    def test_every_status_has_a_glyph(self) -> None:
        from apex.gate.runner import _GLYPH

        for status in GateStatus:
            self.assertIn(status, _GLYPH)



class TestNoSilentInternalErrors(unittest.TestCase):
    """Le filet de securite du runner ne doit pas masquer un bug.

    Regression reelle : un patch a introduit un appel a une fonction inexistante
    dans GATE-07. Le try/except du runner a converti le NameError en FAIL, et la
    suite est restee verte. Un Gate qui ne plante jamais peut donc mentir en
    silence. Ces tests distinguent un echec de controle d'une anomalie du Gate.
    """

    def _assert_no_internal_error(self, report) -> None:
        pass

    def test_no_check_raises_on_an_empty_machine(self) -> None:
        report = run_gate(probe=FakeProbe(), exceptions_path="/nonexistent")
        broken = [c for c in report.checks if "contr\u00f4le en erreur" in c.message]
        self.assertEqual(broken, [], [c.message for c in broken])

    def test_no_check_raises_on_a_healthy_machine(self) -> None:
        report = run_gate(probe=healthy_probe(), exceptions_path="/nonexistent")
        broken = [c for c in report.checks if "contr\u00f4le en erreur" in c.message]
        self.assertEqual(broken, [], [c.message for c in broken])

    def test_no_check_raises_with_side_effects_allowed(self) -> None:
        report = run_gate(
            probe=healthy_probe(), exceptions_path="/nonexistent",
            allow_container_exec=True, allow_network_probe=True,
        )
        broken = [c for c in report.checks if "contr\u00f4le en erreur" in c.message]
        self.assertEqual(broken, [], [c.message for c in broken])


class TestGpuVendorDetection(unittest.TestCase):
    """Machine #1 est un APU AMD : \u00ab pas de NVIDIA \u00bb n'est pas \u00ab pas de GPU \u00bb."""

    def _probe_without_nvidia(self) -> FakeProbe:
        probe = healthy_probe()
        probe.programs.pop("nvidia-smi", None)
        return probe

    def test_amd_igpu_is_not_reported_as_absent(self) -> None:
        probe = self._probe_without_nvidia()
        probe.paths.add("/dev/dxg")
        outcome = gate_07_gpu_from_wsl(ctx(probe))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertEqual(outcome.facts["gpu_vendor"], "other_or_integrated")
        self.assertFalse(outcome.facts["gpu_usable_by_apex"])
        self.assertIn("PAS \u00e9quivalent", outcome.remediation)

    def test_rocm_is_detected_but_not_usable(self) -> None:
        probe = self._probe_without_nvidia()
        probe.programs["rocm-smi"] = "/usr/bin/rocm-smi"
        outcome = gate_07_gpu_from_wsl(ctx(probe))
        self.assertEqual(outcome.facts["gpu_vendor"], "amd_rocm")
        self.assertFalse(outcome.facts["gpu_usable_by_apex"])
        self.assertIn(6, outcome.blocks_phases)

    def test_truly_absent_gpu_is_reported_as_none(self) -> None:
        outcome = gate_07_gpu_from_wsl(ctx(self._probe_without_nvidia()))
        self.assertEqual(outcome.facts["gpu_vendor"], "none")

    def test_nvidia_remains_the_only_usable_vendor(self) -> None:
        outcome = gate_07_gpu_from_wsl(ctx(healthy_probe()))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertEqual(outcome.facts["gpu_vendor"], "nvidia")
        self.assertTrue(outcome.facts["gpu_usable_by_apex"])

    def test_vram_on_integrated_gpu_is_not_pretended(self) -> None:
        probe = self._probe_without_nvidia()
        probe.paths.add("/dev/dxg")
        outcome = gate_09_vram(ctx(probe))
        self.assertIs(outcome.status, GateStatus.UNKNOWN)
        self.assertIsNone(outcome.facts["vram_total_gb"])
        self.assertIn("partag\u00e9e", outcome.message)

    def test_vendor_is_recorded_in_the_fingerprint_facts(self) -> None:
        probe = self._probe_without_nvidia()
        probe.paths.add("/dev/dxg")
        report = run_gate(probe=probe, exceptions_path="/nonexistent")
        self.assertEqual(report.facts()["gpu_vendor"], "other_or_integrated")



if __name__ == "__main__":
    unittest.main()


class TestMultiMachine(unittest.TestCase):
    """Deux machines produisent deux empreintes.

    Contexte réel : le Gate sera exécuté d'abord sur une machine temporaire,
    puis sur la machine cible. Sans ces garanties, la seconde mesure écraserait
    la première, ou pire, un rapport copié d'une machine à l'autre autoriserait
    une phase sur des faits qui ne la concernent pas.
    """

    def test_write_archives_by_fingerprint(self) -> None:
        report = make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "RTX 4090"}]))
        with tempfile.TemporaryDirectory() as tmp:
            report.write(Path(tmp) / "environment.json")
            archived = list((Path(tmp) / "environments").glob("*.json"))
            self.assertEqual(len(archived), 1)
            self.assertIn(report.fingerprint().removeprefix("sha256:")[:12], archived[0].name)

    def test_second_machine_does_not_overwrite_the_first(self) -> None:
        machine_a = make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "RTX 4090"}]))
        machine_b = make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "aucun"}]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "environment.json"
            machine_a.write(path)
            machine_b.write(path)
            self.assertEqual(len(list((Path(tmp) / "environments").glob("*.json"))), 2)

    def test_report_from_another_machine_is_refused(self) -> None:
        report = make_report(check("GATE-14", GateStatus.PASS, cgroups_v2=True))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "environment.json"
            report.write(path)
            load_environment(path, expected_fingerprint=report.fingerprint())
            with self.assertRaises(EnvironmentUnknown):
                load_environment(path, expected_fingerprint="sha256:une-autre-machine")

    def test_environments_can_be_listed(self) -> None:
        from apex.gate.contract import list_environments

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "environment.json"
            make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "A"}])).write(path)
            make_report(check("GATE-08", GateStatus.PASS, gpus=[{"model": "B"}])).write(path)
            found = list_environments(Path(tmp) / "environments")
            self.assertEqual(len(found), 2)
            self.assertEqual(len({e.fingerprint for e in found}), 2)

    def test_machine_label_is_carried_into_the_report(self) -> None:
        report = make_report(check("GATE-05", GateStatus.PASS), machine_label="poste-temporaire")
        self.assertEqual(report.to_dict()["machine_label"], "poste-temporaire")


class TestPythonVersionSupport(unittest.TestCase):
    """MIN_PYTHON n'a qu'une définition ; le seuil est un plancher, pas un plafond."""

    def test_single_source_of_truth(self) -> None:
        from apex.doctor.checks.environment import MIN_PYTHON as doctor_min
        from apex.gate.checks import MIN_PYTHON as gate_min
        from apex.version import MIN_PYTHON as canonical

        self.assertIs(doctor_min, canonical)
        self.assertIs(gate_min, canonical)

    def test_newer_python_passes(self) -> None:
        """Python 3.14 doit passer : le contrôle est un plancher."""
        probe = healthy_probe()
        probe.platform = {**probe.platform, "python": "3.14.4"}
        outcome = gate_05_python(ctx(probe))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertEqual(outcome.facts["python_version"], "3.14.4")

    def test_older_python_fails(self) -> None:
        probe = healthy_probe()
        probe.platform = {**probe.platform, "python": "3.11.9"}
        self.assertIs(gate_05_python(ctx(probe)).status, GateStatus.FAIL)


class TestHardwareThresholdsAreNotACeiling(unittest.TestCase):
    """Le hardware de la machine qui exécute le Gate est une variable
    d'environnement, jamais une limite architecturale codée en dur. Ces
    seuils décrivent un choix de déploiement (poste de dev modeste, machine
    cible future) ; ils ne doivent jamais empêcher APEX de fonctionner
    ailleurs, ni prétendre qu'une machine cible est disponible aujourd'hui."""

    def test_gate_and_doctor_share_one_source_of_truth(self) -> None:
        """Avant ce correctif : MIN_FREE_GB valait 150 dans le Gate et 80
        (divergent) dans le doctor — deux seuils pour la même question."""
        from apex.doctor.checks.platform import _DEFAULT_THRESHOLDS as doctor_defaults
        from apex.gate.checks import _DEFAULT_THRESHOLDS as gate_defaults

        self.assertEqual(gate_defaults, doctor_defaults)

    def test_low_ram_threshold_from_config_makes_a_modest_machine_pass(self) -> None:
        """Un poste de développement avec moins de 24 Go de RAM ne doit pas
        être signalé DEGRADED si le déploiement a explicitement choisi un
        seuil plus bas — la valeur par défaut n'est pas un plafond d'APEX."""
        document = base_config()
        document["gate"] = {"hardware": {"min_ram_gb": 2}}
        probe = healthy_probe()
        probe.files["/proc/meminfo"] = "MemTotal:        4000000 kB\nMemAvailable:     2000000 kB\n"
        outcome = gate_12_ram(GateContext(probe=probe, config=parse_config(document), timeout_s=1.0))
        self.assertIs(outcome.status, GateStatus.PASS)

    def test_default_threshold_still_flags_the_same_modest_machine(self) -> None:
        """Sans reconfiguration explicite, le comportement par défaut est
        inchangé (aucune limite existante modifiée en silence)."""
        probe = healthy_probe()
        probe.files["/proc/meminfo"] = "MemTotal:        4000000 kB\nMemAvailable:     2000000 kB\n"
        outcome = gate_12_ram(ctx(probe))
        self.assertIs(outcome.status, GateStatus.DEGRADED)

    def test_target_pool_vram_mismatch_is_not_flagged(self) -> None:
        """Un pool status=target bien plus gros que le matériel mesuré ne
        doit jamais déclencher le DEGRADED « déclaré vs mesuré » : c'est
        l'écart attendu entre le poste de dev et la machine cible future."""
        document = base_config()
        document["compute"]["pools"]["future-machine"] = {
            "location": "local", "trust_level": "trusted", "status": "target",
            "capabilities": ["inference"], "gpus": [{"model": "TBD", "vram_gb": 200}],
        }
        outcome = gate_09_vram(GateContext(probe=healthy_probe(), config=parse_config(document), timeout_s=1.0))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertEqual(outcome.facts["vram_declared_gb"], 24.0)  # ne compte que local-gpu (ACTIVE)

    def test_active_pool_vram_mismatch_is_still_flagged(self) -> None:
        """La correction n'a pas désactivé le contrôle : un vrai pool ACTIVE
        mal déclaré doit toujours produire un DEGRADED."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 200
        outcome = gate_09_vram(GateContext(probe=healthy_probe(), config=parse_config(document), timeout_s=1.0))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
