"""Tests d'`apex.doctor.launcher` — orchestration DIAGNOSTIC -> AUTO-REPAIR
-> VERIFY -> GATE. Utilise un jeu de checks SYNTHÉTIQUES injecté (`checks=`)
plutôt que `apex.doctor.runner.ALL_CHECKS` (l'état matériel réel de cette
machine — GPU, Docker, Ollama — ne doit jamais déterminer si CE test passe :
même discipline que `FakeProbe` pour `apex doctor`)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from apex.doctor.check import CheckContext, CheckResult, Severity
from apex.doctor.launcher import StartupStatus, render_text, run_preflight_and_repair
from apex.doctor.startup_history import read_history


def _pass_check(ctx: CheckContext) -> CheckResult:
    return CheckResult(name="synthetic.ok", category="synthetic", severity=Severity.PASS, message="ok")


def _unfixable_fail_check(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        name="synthetic.unfixable", category="synthetic", severity=Severity.FAIL,
        message="échec réel sans réparation connue",
    )


def _fixable_service_down_check(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        name="service.postgres", category="services", severity=Severity.FAIL,
        message="postgres injoignable sur 127.0.0.1:5432", remediation="docker compose up -d",
        facts={"host": "127.0.0.1", "port": 5432},
    )


class TestDryRun(unittest.TestCase):
    def test_a_dry_run_produces_a_plan_and_executes_nothing(self) -> None:
        outcome = run_preflight_and_repair(
            dry_run=True, checks=(("service.postgres", _fixable_service_down_check),),
            history_path=None,
        )
        self.assertIs(outcome.status, StartupStatus.DRY_RUN)
        self.assertIsNone(outcome.verify_report)
        self.assertIsNone(outcome.gate_authorized)
        self.assertEqual(len(outcome.plans), 1)
        self.assertFalse(outcome.repair_outcomes[0].attempted)

    def test_b_render_text_says_dry_run_and_no_repairs_executed(self) -> None:
        outcome = run_preflight_and_repair(
            dry_run=True, checks=(("service.postgres", _fixable_service_down_check),),
            history_path=None,
        )
        text = render_text(outcome)
        self.assertIn("dry-run", text)
        self.assertIn("service.postgres", text)


class TestBlockedDoctor(unittest.TestCase):
    def test_a_an_unfixable_failure_blocks_before_gate_is_ever_called(self) -> None:
        outcome = run_preflight_and_repair(
            dry_run=False, checks=(("synthetic.unfixable", _unfixable_fail_check),),
            history_path=None,
        )
        self.assertIs(outcome.status, StartupStatus.BLOCKED_DOCTOR)
        self.assertIsNotNone(outcome.verify_report)
        self.assertIs(outcome.verify_report.worst, Severity.FAIL)
        # Gate n'a jamais été sollicité — jamais un contournement du doctor.
        self.assertIsNone(outcome.gate_authorized)

    def test_b_a_passing_environment_never_blocks_on_doctor(self) -> None:
        outcome = run_preflight_and_repair(
            dry_run=False, checks=(("synthetic.ok", _pass_check),), history_path=None,
        )
        self.assertIsNot(outcome.status, StartupStatus.BLOCKED_DOCTOR)


class TestHistoryIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.history_path = os.path.join(self.tmpdir, "startup_history.jsonl")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_a_a_real_run_is_recorded_in_history(self) -> None:
        run_preflight_and_repair(
            dry_run=True, checks=(("service.postgres", _fixable_service_down_check),),
            history_path=self.history_path,
        )
        history = read_history(path=self.history_path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].problems_detected, ("service.postgres",))
        self.assertEqual(history[0].final_status, "dry_run")

    def test_b_history_path_none_writes_nothing(self) -> None:
        run_preflight_and_repair(
            dry_run=True, checks=(("service.postgres", _fixable_service_down_check),),
            history_path=None,
        )
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "startup_history.jsonl")))


if __name__ == "__main__":
    unittest.main()
