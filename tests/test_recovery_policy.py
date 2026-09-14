"""Tests d'`apex.recovery` — classification (`classify.py`) et politique
(`policy.py`). Purs, déterministes, aucune dépendance DB/sandbox réelle —
la partie orchestration (spawn/retry réel via `AgentRuntime`) est testée
séparément dans `tests/test_missions_execution_recovery.py`."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from apex.config.schema import RecoveryPolicy as RecoveryPolicyConfig
from apex.errors import IsolationUnavailable, ModelErrorCategory, ModelProviderError
from apex.obs.status import Measurement
from apex.recovery.classify import classify_provider_error, classify_sandbox_outcome
from apex.recovery.model import AttemptHistory, AttemptRecord, FailureCategory, RecoveryAction
from apex.recovery.policy import decide
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult


def _sandbox_result(status: ExecutionStatus) -> SandboxResult:
    return SandboxResult(
        mission_id="m", clone_id="c", status=status, isolation_level=IsolationLevel.NAMESPACED,
        exit_code=Measurement.unknown(), signal=Measurement.unknown(), elapsed_s=Measurement.real(1.0),
        cpu_time_s=Measurement.unknown(), memory_peak_mb=Measurement.unknown(), pids_peak=Measurement.unknown(),
        stdout="", stderr="", stdout_truncated=False, stderr_truncated=False,
    )


def _isolation_unavailable() -> IsolationUnavailable:
    return IsolationUnavailable("test", required="namespaced", available="rlimit_only")


def _provider_error(category: ModelErrorCategory) -> ModelProviderError:
    return ModelProviderError("test", endpoint_url="http://x", detail="x", category=category)


class TestClassifySandboxOutcome(unittest.TestCase):
    def test_isolation_unavailable_is_sandbox_failure(self) -> None:
        self.assertIs(
            classify_sandbox_outcome(isolation_unavailable=_isolation_unavailable()),
            FailureCategory.SANDBOX_FAILURE,
        )

    def test_timeout_status_is_timeout(self) -> None:
        self.assertIs(
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.TIMEOUT)),
            FailureCategory.TIMEOUT,
        )

    def test_cancelled_status_is_cancelled(self) -> None:
        self.assertIs(
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.CANCELLED)),
            FailureCategory.CANCELLED,
        )

    def test_resource_statuses_are_resource_exhausted(self) -> None:
        for status in (
            ExecutionStatus.OOM_KILLED, ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
            ExecutionStatus.PROCESS_LIMIT_EXCEEDED, ExecutionStatus.DISK_LIMIT_EXCEEDED,
        ):
            with self.subTest(status=status):
                self.assertIs(
                    classify_sandbox_outcome(result=_sandbox_result(status)),
                    FailureCategory.RESOURCE_EXHAUSTED,
                )

    def test_permission_denied_is_sandbox_failure(self) -> None:
        """Échec de CRÉATION de la sandbox (voir `_creation_failure_result`
        dans les deux backends) — un problème d'infrastructure, jamais
        confondu avec un échec du candidat exécuté."""
        self.assertIs(
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.PERMISSION_DENIED)),
            FailureCategory.SANDBOX_FAILURE,
        )

    def test_generic_failed_is_execution_failure(self) -> None:
        self.assertIs(
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.FAILED)),
            FailureCategory.EXECUTION_FAILURE,
        )

    def test_network_denied_is_execution_failure_not_a_dedicated_category(self) -> None:
        self.assertIs(
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.NETWORK_DENIED)),
            FailureCategory.EXECUTION_FAILURE,
        )

    def test_success_refuses_to_be_classified(self) -> None:
        with self.assertRaises(ValueError):
            classify_sandbox_outcome(result=_sandbox_result(ExecutionStatus.SUCCESS))

    def test_requires_exactly_one_argument(self) -> None:
        with self.assertRaises(ValueError):
            classify_sandbox_outcome()
        with self.assertRaises(ValueError):
            classify_sandbox_outcome(
                result=_sandbox_result(ExecutionStatus.FAILED), isolation_unavailable=_isolation_unavailable(),
            )

    def test_every_execution_status_is_covered(self) -> None:
        """Vocabulaire `ExecutionStatus` fermé — aucune valeur ne doit
        silencieusement échapper à la classification (hors SUCCESS, refusé
        explicitement ci-dessus)."""
        for status in ExecutionStatus:
            if status is ExecutionStatus.SUCCESS:
                continue
            with self.subTest(status=status):
                classify_sandbox_outcome(result=_sandbox_result(status))  # ne doit jamais lever


class TestClassifyProviderError(unittest.TestCase):
    def test_offline_is_provider_unavailable(self) -> None:
        self.assertIs(
            classify_provider_error(_provider_error(ModelErrorCategory.OFFLINE)),
            FailureCategory.PROVIDER_UNAVAILABLE,
        )

    def test_reached_but_failed_categories_are_provider_failure(self) -> None:
        for category in (
            ModelErrorCategory.RATE_LIMITED, ModelErrorCategory.AUTH_ERROR,
            ModelErrorCategory.CONTEXT_TOO_LONG, ModelErrorCategory.PROVIDER_ERROR,
            ModelErrorCategory.UNKNOWN,
        ):
            with self.subTest(category=category):
                self.assertIs(
                    classify_provider_error(_provider_error(category)), FailureCategory.PROVIDER_FAILURE,
                )

    def test_model_timeout_is_recovery_timeout(self) -> None:
        self.assertIs(
            classify_provider_error(_provider_error(ModelErrorCategory.TIMEOUT)), FailureCategory.TIMEOUT,
        )

    def test_every_model_error_category_is_covered(self) -> None:
        for category in ModelErrorCategory:
            with self.subTest(category=category):
                classify_provider_error(_provider_error(category))  # ne doit jamais lever


class TestRecoveryPolicyDecide(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RecoveryPolicyConfig.parse({"max_attempts": 3, "resource_exhausted_max_retries": 1})

    def test_sandbox_failure_always_blocks_never_retries(self) -> None:
        """§5 de la mission : « ne pas retry indéfiniment -> BLOCK »."""
        for attempt in (1, 2, 3):
            d = decide(FailureCategory.SANDBOX_FAILURE, attempt_number=attempt, policy=self.policy)
            self.assertIs(d.action, RecoveryAction.BLOCK)

    def test_cancelled_never_auto_retries(self) -> None:
        d = decide(FailureCategory.CANCELLED, attempt_number=1, policy=self.policy)
        self.assertIs(d.action, RecoveryAction.BLOCK)

    def test_timeout_retries_until_budget_then_blocks(self) -> None:
        d1 = decide(FailureCategory.TIMEOUT, attempt_number=1, policy=self.policy)
        self.assertIs(d1.action, RecoveryAction.RETRY)
        d2 = decide(FailureCategory.TIMEOUT, attempt_number=2, policy=self.policy)
        self.assertIs(d2.action, RecoveryAction.RETRY)
        d3 = decide(FailureCategory.TIMEOUT, attempt_number=3, policy=self.policy)
        self.assertIs(d3.action, RecoveryAction.BLOCK)  # attempt_number == max_attempts : épuisé

    def test_no_action_ever_exceeds_max_attempts(self) -> None:
        """Aucune boucle infinie, quelle que soit la catégorie."""
        for category in FailureCategory:
            with self.subTest(category=category):
                d = decide(category, attempt_number=self.policy.max_attempts, policy=self.policy)
                self.assertIn(d.action, (RecoveryAction.BLOCK, RecoveryAction.ESCALATE))

    def test_invalid_candidate_retries_with_strategy_change(self) -> None:
        d = decide(FailureCategory.INVALID_CANDIDATE, attempt_number=1, policy=self.policy)
        self.assertIs(d.action, RecoveryAction.RETRY)
        self.assertIsNotNone(d.strategy_change)

    def test_incorrect_solution_retries_then_escalates_not_silently_blocks(self) -> None:
        d1 = decide(FailureCategory.INCORRECT_SOLUTION, attempt_number=1, policy=self.policy)
        self.assertIs(d1.action, RecoveryAction.RETRY)
        d_exhausted = decide(FailureCategory.INCORRECT_SOLUTION, attempt_number=3, policy=self.policy)
        self.assertIs(d_exhausted.action, RecoveryAction.ESCALATE)

    def test_provider_unavailable_reassigns_only_with_a_real_alternative(self) -> None:
        blocked = decide(
            FailureCategory.PROVIDER_UNAVAILABLE, attempt_number=1, policy=self.policy, has_alternate_target=False,
        )
        self.assertIs(blocked.action, RecoveryAction.BLOCK)
        reassigned = decide(
            FailureCategory.PROVIDER_UNAVAILABLE, attempt_number=1, policy=self.policy, has_alternate_target=True,
        )
        self.assertIs(reassigned.action, RecoveryAction.REASSIGN)

    def test_provider_failure_prefers_reassign_over_plain_retry_when_alternative_exists(self) -> None:
        retried = decide(
            FailureCategory.PROVIDER_FAILURE, attempt_number=1, policy=self.policy, has_alternate_target=False,
        )
        self.assertIs(retried.action, RecoveryAction.RETRY)
        reassigned = decide(
            FailureCategory.PROVIDER_FAILURE, attempt_number=1, policy=self.policy, has_alternate_target=True,
        )
        self.assertIs(reassigned.action, RecoveryAction.REASSIGN)

    def test_resource_exhausted_uses_its_own_stricter_budget(self) -> None:
        """`resource_exhausted_max_retries=1` : une seule nouvelle tentative,
        jamais autant que `max_attempts` (3) le permettrait pour les autres
        catégories."""
        d1 = decide(FailureCategory.RESOURCE_EXHAUSTED, attempt_number=1, policy=self.policy)
        self.assertIs(d1.action, RecoveryAction.RETRY)
        d2 = decide(FailureCategory.RESOURCE_EXHAUSTED, attempt_number=2, policy=self.policy)
        self.assertIs(d2.action, RecoveryAction.BLOCK)

    def test_decision_carries_full_observable_context(self) -> None:
        d = decide(
            FailureCategory.TIMEOUT, attempt_number=1, policy=self.policy, previous_failure="boom",
        )
        self.assertEqual(d.category, FailureCategory.TIMEOUT)
        self.assertEqual(d.attempt_number, 1)
        self.assertEqual(d.max_attempts, 3)
        self.assertEqual(d.previous_failure, "boom")
        as_dict = d.to_dict()
        self.assertEqual(as_dict["action"], "retry")

    def test_rejects_attempt_number_below_one(self) -> None:
        with self.assertRaises(ValueError):
            decide(FailureCategory.TIMEOUT, attempt_number=0, policy=self.policy)

    def test_every_category_has_a_rule_and_never_raises(self) -> None:
        for category in FailureCategory:
            for attempt in (1, 2, 3):
                with self.subTest(category=category, attempt=attempt):
                    decide(category, attempt_number=attempt, policy=self.policy)  # ne doit jamais lever


class TestAttemptRecordAndHistory(unittest.TestCase):
    def test_attempt_record_round_trips_to_dict(self) -> None:
        record = AttemptRecord(
            attempt_number=1, provider_name="LinuxNamespaceSandbox", sandbox_level="namespaced",
            category=FailureCategory.TIMEOUT, reason="délai dépassé", strategy="nouveau clone, même pool",
            judge_result=None, started_at=datetime.now(timezone.utc), duration_s=2.5,
            reassigned=False, decision=RecoveryAction.RETRY,
        )
        as_dict = record.to_dict()
        self.assertEqual(as_dict["category"], "timeout")
        self.assertEqual(as_dict["decision"], "retry")
        self.assertIn("started_at", as_dict)

    def test_success_record_carries_no_category_or_decision(self) -> None:
        record = AttemptRecord(
            attempt_number=2, provider_name="LinuxNamespaceSandbox", sandbox_level="namespaced",
            category=None, reason="succès", strategy=None, judge_result=None,
            started_at=datetime.now(timezone.utc), duration_s=1.0, reassigned=True, decision=None,
        )
        self.assertIsNone(record.to_dict()["category"])
        self.assertIsNone(record.to_dict()["decision"])

    def test_history_accumulates_in_order(self) -> None:
        history = AttemptHistory()
        self.assertEqual(history.attempt_count, 0)
        self.assertIsNone(history.last)
        r1 = AttemptRecord(
            attempt_number=1, provider_name="p", sandbox_level="namespaced", category=FailureCategory.TIMEOUT,
            reason="x", strategy=None, judge_result=None, started_at=datetime.now(timezone.utc),
            duration_s=1.0, reassigned=False, decision=RecoveryAction.RETRY,
        )
        history.add(r1)
        self.assertEqual(history.attempt_count, 1)
        self.assertIs(history.last, r1)


if __name__ == "__main__":
    unittest.main()
