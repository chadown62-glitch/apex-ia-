"""Tests de Tier0Judge — jugement pur, aucun réseau, aucune DB. Vérifie que
JudgeTier délègue INTÉGRALEMENT à OracleProvider (zéro logique de jugement
dupliquée) et ne tente jamais d'escalade vers TIER_1/TIER_2 (PLANNED)."""

from __future__ import annotations

import unittest

from apex.judge.model import JudgeTierLevel, JudgeVerdict
from apex.judge.provider import get_default_judge_tier
from apex.judge.tier0 import Tier0Judge
from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.obs.status import Measurement
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task, TaskSplit


def a_task(**overrides) -> Task:
    return Task.new(
        split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
        command=("true",), created_by="human", **overrides,
    )


def a_result(status: ExecutionStatus, *, exit_code: Measurement | None = None) -> SandboxResult:
    return SandboxResult(
        mission_id="m1", clone_id="c1", status=status, isolation_level=IsolationLevel.NAMESPACED,
        exit_code=exit_code if exit_code is not None else Measurement.unknown(),
        signal=Measurement.unknown(), elapsed_s=Measurement.real(0.1), cpu_time_s=Measurement.unknown(),
        memory_peak_mb=Measurement.unknown(), pids_peak=Measurement.unknown(),
        stdout="", stderr="", stdout_truncated=False, stderr_truncated=False,
    )


class _FixedOracle(OracleProvider):
    """Double de test : renvoie toujours le même OracleResult, quel que soit
    l'argument — prouve que Tier0Judge ne fait AUCUN calcul lui-même, il
    ne fait que transmettre ce que l'oracle injecté a produit."""

    def __init__(self, result: OracleResult) -> None:
        self._result = result
        self.calls: list[tuple[SandboxResult, Task]] = []

    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        self.calls.append((sandbox_result, task))
        return self._result


class TestTier0JudgeDelegatesToOracle(unittest.TestCase):
    def test_verdict_matches_the_injected_oracle_verbatim(self) -> None:
        fixed = OracleResult(verdict=OracleVerdict.PASS, detail="stub-detail")
        judge = Tier0Judge(oracle=_FixedOracle(fixed))
        verdict = judge.evaluate(sandbox_result=a_result(ExecutionStatus.SUCCESS), task=a_task())
        self.assertIs(verdict.oracle_result, fixed)

    def test_oracle_is_called_exactly_once_with_the_same_arguments(self) -> None:
        fixed = OracleResult(verdict=OracleVerdict.FAIL, detail="x")
        fake = _FixedOracle(fixed)
        judge = Tier0Judge(oracle=fake)
        result = a_result(ExecutionStatus.FAILED)
        task = a_task()
        judge.evaluate(sandbox_result=result, task=task)
        self.assertEqual(len(fake.calls), 1)
        self.assertIs(fake.calls[0][0], result)
        self.assertIs(fake.calls[0][1], task)

    def test_tier_is_always_tier_0(self) -> None:
        judge = Tier0Judge(oracle=_FixedOracle(OracleResult(verdict=OracleVerdict.UNKNOWN, detail="x")))
        verdict = judge.evaluate(sandbox_result=a_result(ExecutionStatus.CANCELLED), task=a_task())
        self.assertEqual(verdict.tier, JudgeTierLevel.TIER_0)

    def test_never_escalates_even_on_unknown_verdict(self) -> None:
        """Un verdict UNKNOWN de tier 0 reste UNKNOWN — jamais transformé en
        tentative d'escalade vers un palier qui n'existe pas."""
        judge = Tier0Judge(oracle=_FixedOracle(OracleResult(verdict=OracleVerdict.UNKNOWN, detail="x")))
        verdict = judge.evaluate(sandbox_result=a_result(ExecutionStatus.CANCELLED), task=a_task())
        self.assertEqual(verdict.tier, JudgeTierLevel.TIER_0)
        self.assertEqual(verdict.oracle_result.verdict, OracleVerdict.UNKNOWN)

class TestTier0JudgeRealOracleIntegration(unittest.TestCase):
    """Contre le VRAI ExitCodeOracle (par défaut), pas un double — même
    esprit que test_oracles.py : prouve le comportement de bout en bout,
    pas seulement la délégation."""

    def setUp(self) -> None:
        self.judge = get_default_judge_tier()
        self.task = a_task()

    def test_success_yields_pass_via_real_oracle(self) -> None:
        verdict = self.judge.evaluate(
            sandbox_result=a_result(ExecutionStatus.SUCCESS, exit_code=Measurement.real(0)), task=self.task,
        )
        self.assertEqual(verdict.oracle_result.verdict, OracleVerdict.PASS)
        self.assertEqual(verdict.tier, JudgeTierLevel.TIER_0)

    def test_failed_yields_fail_via_real_oracle(self) -> None:
        verdict = self.judge.evaluate(
            sandbox_result=a_result(ExecutionStatus.FAILED, exit_code=Measurement.real(1)), task=self.task,
        )
        self.assertEqual(verdict.oracle_result.verdict, OracleVerdict.FAIL)

    def test_notes_disclose_tier_1_and_2_are_planned(self) -> None:
        verdict = self.judge.evaluate(
            sandbox_result=a_result(ExecutionStatus.SUCCESS, exit_code=Measurement.real(0)), task=self.task,
        )
        self.assertIn("PLANNED", verdict.notes)

    def test_get_default_judge_tier_returns_tier0judge(self) -> None:
        self.assertIsInstance(self.judge, Tier0Judge)


class TestJudgeVerdictShape(unittest.TestCase):
    def test_judge_verdict_wraps_oracle_result_never_replaces_its_vocabulary(self) -> None:
        """JudgeVerdict ne réinvente pas de second vocabulaire de correction
        — il porte un OracleResult tel quel."""
        fields = JudgeVerdict.__dataclass_fields__
        self.assertIn("oracle_result", fields)
        self.assertNotIn("verdict", fields)


if __name__ == "__main__":
    unittest.main()
