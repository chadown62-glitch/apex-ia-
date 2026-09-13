"""Tests d'`apex.doctor.repair` — passe « Self-Healing Launcher » (N+19,
AXE B). Classification pure (aucun mock nécessaire pour la logique
elle-même) + exécution via un `CommandRunner` injecté (jamais un vrai
`subprocess.run` dans ces tests, même discipline que `FakeProbe` pour
`apex doctor`)."""

from __future__ import annotations

import subprocess
import unittest

from apex.doctor.check import CheckResult, Severity
from apex.doctor.repair import (
    RepairAction,
    apply_repairs,
    classify,
    plan_repairs,
)


def a_result(**overrides) -> CheckResult:
    defaults = dict(
        name="service.postgres", category="services", severity=Severity.FAIL,
        message="postgres injoignable sur 127.0.0.1:5432", remediation="docker compose up -d",
        facts={"host": "127.0.0.1", "port": 5432},
    )
    defaults.update(overrides)
    return CheckResult(**defaults)


class TestClassify(unittest.TestCase):
    def test_a_pass_is_always_retry_only_trivial(self) -> None:
        plan = classify(a_result(severity=Severity.PASS, message="ok"))
        self.assertIs(plan.action, RepairAction.RETRY_ONLY)

    def test_b_skip_is_always_retry_only_trivial(self) -> None:
        plan = classify(a_result(severity=Severity.SKIP, message="ignoré"))
        self.assertIs(plan.action, RepairAction.RETRY_ONLY)

    def test_c_gpu_absence_is_always_manual_never_auto_fixed(self) -> None:
        """Une limite matérielle réelle (§21/§30 : ne jamais prétendre
        réparer le matériel) reste MANUAL_REQUIRED, quelle que soit la
        sévérité."""
        plan = classify(a_result(name="gpu.present", severity=Severity.WARN, message="pas de GPU"))
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)

    def test_d_docker_isolation_is_always_manual(self) -> None:
        plan = classify(a_result(name="docker.isolation", severity=Severity.WARN, message="runsc absent"))
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)

    def test_e_unknown_check_name_defaults_to_manual_never_auto_fix(self) -> None:
        """§ liste blanche : l'ABSENCE de règle produit MANUAL_REQUIRED,
        jamais SAFE_AUTO_FIX par défaut."""
        plan = classify(a_result(name="totally.unknown.check", severity=Severity.FAIL, remediation=""))
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)

    def test_f_docker_daemon_unreachable_is_safe_auto_fix(self) -> None:
        plan = classify(
            a_result(
                name="docker.present", severity=Severity.FAIL, message="démon Docker injoignable",
                remediation="sudo service docker start", facts={},
            )
        )
        self.assertIs(plan.action, RepairAction.SAFE_AUTO_FIX)
        self.assertEqual(plan.command, ("service", "docker", "start"))

    def test_g_docker_absent_entirely_is_manual_never_installed_automatically(self) -> None:
        """§33 : n'installe jamais Docker lui-même — seul le DÉMARRAGE d'un
        Docker déjà installé est une SAFE_AUTO_FIX."""
        plan = classify(
            a_result(name="docker.present", severity=Severity.FAIL, message="docker introuvable", facts={})
        )
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)

    def test_h_service_postgres_down_is_safe_auto_fix_scoped_to_that_service(self) -> None:
        plan = classify(a_result(name="service.postgres"))
        self.assertIs(plan.action, RepairAction.SAFE_AUTO_FIX)
        self.assertEqual(plan.command, ("docker", "compose", "up", "-d", "postgres"))

    def test_i_service_redis_down_is_safe_auto_fix_scoped_to_redis_only(self) -> None:
        plan = classify(
            a_result(
                name="service.redis", message="redis injoignable sur 127.0.0.1:6379",
                facts={"host": "127.0.0.1", "port": 6379},
            )
        )
        self.assertEqual(plan.command, ("docker", "compose", "up", "-d", "redis"))

    def test_j_ollama_service_down_is_safe_auto_fix_but_never_downloads_models(self) -> None:
        """§21 : le service Ollama peut être démarré (docker compose up -d
        ollama, ne fait QUE démarrer le conteneur), mais `ollama.models`
        (absence de modèle) n'a AUCUNE règle — jamais un téléchargement
        automatique."""
        plan = classify(
            a_result(
                name="service.ollama", message="ollama injoignable sur 127.0.0.1:11434",
                facts={"host": "127.0.0.1", "port": 11434},
            )
        )
        self.assertEqual(plan.command, ("docker", "compose", "up", "-d", "ollama"))

        model_plan = classify(
            a_result(
                name="ollama.models", severity=Severity.WARN, message="modèle de référence absent",
                remediation="ollama pull qwen2.5-coder:32b-instruct", facts={},
            )
        )
        self.assertIs(model_plan.action, RepairAction.MANUAL_REQUIRED)

    def test_k_service_down_without_the_exact_remediation_string_is_not_matched(self) -> None:
        """La règle ne se déclenche que sur le contrat RÉEL (`remediation ==
        "docker compose up -d"`, `host`/`port` réels dans `facts`) — jamais
        une correspondance approximative."""
        plan = classify(a_result(name="service.postgres", remediation="autre chose"))
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)


class TestApplyRepairs(unittest.TestCase):
    def test_a_dry_run_never_calls_the_runner(self) -> None:
        calls: list[tuple] = []

        def fake_runner(argv, *, timeout_s):
            calls.append(argv)
            raise AssertionError("le runner ne doit jamais être appelé en dry-run")

        plans = plan_repairs((a_result(),))
        outcomes = apply_repairs(plans, dry_run=True, runner=fake_runner)
        self.assertEqual(calls, [])
        self.assertFalse(outcomes[0].attempted)
        self.assertIn("dry-run", outcomes[0].detail)

    def test_b_manual_required_is_never_attempted_even_without_dry_run(self) -> None:
        calls: list[tuple] = []

        def fake_runner(argv, *, timeout_s):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        plans = plan_repairs((a_result(name="gpu.present", severity=Severity.WARN),))
        outcomes = apply_repairs(plans, dry_run=False, runner=fake_runner)
        self.assertEqual(calls, [])
        self.assertFalse(outcomes[0].attempted)

    def test_c_safe_auto_fix_is_really_executed_via_the_injected_runner(self) -> None:
        received: list[tuple] = []

        def fake_runner(argv, *, timeout_s):
            received.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        plans = plan_repairs((a_result(),))
        outcomes = apply_repairs(plans, dry_run=False, runner=fake_runner)
        self.assertEqual(received, [("docker", "compose", "up", "-d", "postgres")])
        self.assertTrue(outcomes[0].attempted)
        self.assertTrue(outcomes[0].succeeded)

    def test_d_a_failing_repair_command_is_reported_never_raised(self) -> None:
        def fake_runner(argv, *, timeout_s):
            return subprocess.CompletedProcess(argv, 1, "", "erreur réelle")

        plans = plan_repairs((a_result(),))
        outcomes = apply_repairs(plans, dry_run=False, runner=fake_runner)
        self.assertTrue(outcomes[0].attempted)
        self.assertFalse(outcomes[0].succeeded)

    def test_e_a_runner_exception_is_caught_never_propagated(self) -> None:
        def fake_runner(argv, *, timeout_s):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s)

        plans = plan_repairs((a_result(),))
        outcomes = apply_repairs(plans, dry_run=False, runner=fake_runner)
        self.assertTrue(outcomes[0].attempted)
        self.assertFalse(outcomes[0].succeeded)
        self.assertIn("TimeoutExpired", outcomes[0].detail)


if __name__ == "__main__":
    unittest.main()
