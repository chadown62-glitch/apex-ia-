"""Tests d'ExitCodeOracle — jugement pur, aucun réseau, aucune DB, aucune
exécution réelle : uniquement la logique de mapping ExecutionStatus ->
OracleVerdict contre des SandboxResult synthétiques."""

from __future__ import annotations

import unittest

from apex.obs.status import Measurement
from apex.oracles.exit_code import ExitCodeOracle
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task, TaskSplit


def a_task(**overrides) -> Task:
    return Task.new(
        split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
        command=("true",), created_by="human",
        **overrides,
    )


def a_result(status: ExecutionStatus, *, exit_code: Measurement | None = None, error_detail: str | None = None) -> SandboxResult:
    return SandboxResult(
        mission_id="m1", clone_id="c1", status=status, isolation_level=IsolationLevel.NAMESPACED,
        exit_code=exit_code if exit_code is not None else Measurement.unknown(),
        signal=Measurement.unknown(), elapsed_s=Measurement.real(0.1), cpu_time_s=Measurement.unknown(),
        memory_peak_mb=Measurement.unknown(), pids_peak=Measurement.unknown(),
        stdout="", stderr="", stdout_truncated=False, stderr_truncated=False, error_detail=error_detail,
    )


class TestExitCodeOracle(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = ExitCodeOracle()
        self.task = a_task()

    def test_success_is_pass(self) -> None:
        result = a_result(ExecutionStatus.SUCCESS, exit_code=Measurement.real(0))
        verdict = self.oracle.judge(sandbox_result=result, task=self.task)
        self.assertEqual(verdict.verdict, OracleVerdict.PASS)

    def test_failed_is_fail(self) -> None:
        result = a_result(ExecutionStatus.FAILED, exit_code=Measurement.real(1))
        verdict = self.oracle.judge(sandbox_result=result, task=self.task)
        self.assertEqual(verdict.verdict, OracleVerdict.FAIL)

    def test_timeout_is_timeout(self) -> None:
        result = a_result(ExecutionStatus.TIMEOUT)
        verdict = self.oracle.judge(sandbox_result=result, task=self.task)
        self.assertEqual(verdict.verdict, OracleVerdict.TIMEOUT)

    def test_cancelled_is_unknown_never_a_guessed_fail(self) -> None:
        result = a_result(ExecutionStatus.CANCELLED)
        verdict = self.oracle.judge(sandbox_result=result, task=self.task)
        self.assertEqual(verdict.verdict, OracleVerdict.UNKNOWN)

    def test_infrastructure_failures_are_error_not_fail(self) -> None:
        """OOM/limite ressource/réseau refusé/permission refusée : jamais
        confondus avec une réponse incorrecte de la tâche elle-même."""
        for status in (
            ExecutionStatus.OOM_KILLED,
            ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
            ExecutionStatus.PROCESS_LIMIT_EXCEEDED,
            ExecutionStatus.DISK_LIMIT_EXCEEDED,
            ExecutionStatus.NETWORK_DENIED,
            ExecutionStatus.PERMISSION_DENIED,
        ):
            with self.subTest(status=status):
                result = a_result(status)
                verdict = self.oracle.judge(sandbox_result=result, task=self.task)
                self.assertEqual(verdict.verdict, OracleVerdict.ERROR)

    def test_error_detail_includes_error_detail_when_present(self) -> None:
        result = a_result(ExecutionStatus.OOM_KILLED, error_detail="killed: cgroup memory.max")
        verdict = self.oracle.judge(sandbox_result=result, task=self.task)
        self.assertIn("killed: cgroup memory.max", verdict.detail)

    def test_all_five_required_categories_are_reachable(self) -> None:
        """Exigence explicite (GO Phase 5) : succès / échec / erreur
        d'exécution / timeout / résultat inconnu-non évaluable — les cinq
        doivent être atteignables, aucune de plus, aucune de moins."""
        reachable = {
            self.oracle.judge(sandbox_result=a_result(ExecutionStatus.SUCCESS, exit_code=Measurement.real(0)), task=self.task).verdict,
            self.oracle.judge(sandbox_result=a_result(ExecutionStatus.FAILED, exit_code=Measurement.real(1)), task=self.task).verdict,
            self.oracle.judge(sandbox_result=a_result(ExecutionStatus.TIMEOUT), task=self.task).verdict,
            self.oracle.judge(sandbox_result=a_result(ExecutionStatus.CANCELLED), task=self.task).verdict,
            self.oracle.judge(sandbox_result=a_result(ExecutionStatus.OOM_KILLED), task=self.task).verdict,
        }
        self.assertEqual(
            reachable,
            {OracleVerdict.PASS, OracleVerdict.FAIL, OracleVerdict.TIMEOUT, OracleVerdict.UNKNOWN, OracleVerdict.ERROR},
        )

    def test_never_raises(self) -> None:
        """Un jugement impossible à porter est UNKNOWN, jamais une exception."""
        for status in ExecutionStatus:
            with self.subTest(status=status):
                result = a_result(status)
                verdict = self.oracle.judge(sandbox_result=result, task=self.task)
                self.assertIsInstance(verdict.verdict, OracleVerdict)


if __name__ == "__main__":
    unittest.main()
