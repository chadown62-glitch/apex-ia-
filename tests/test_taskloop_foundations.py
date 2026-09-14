"""Tests des fondations N+27 (`apex.taskloop`) — mission N+26.

Ce module ne teste AUCUNE boucle autonome (elle n'existe pas) : uniquement
la machine à états des étapes (§10) et la fonction pure de garde-fous
(§11), toutes deux réelles et exécutables dès aujourd'hui."""

from __future__ import annotations

import unittest

from apex.sandbox.model import CancellationToken
from apex.taskloop import (
    InvalidTaskLoopTransition,
    StopReason,
    TaskLoopStage,
    TaskLoopStopCriteria,
    assert_stage_transition_allowed,
    check_stop,
)


class TestStageTransitions(unittest.TestCase):
    def test_the_full_happy_path_is_legal(self) -> None:
        path = [
            TaskLoopStage.OBJECTIVE, TaskLoopStage.TASK_GENERATION, TaskLoopStage.PLAN,
            TaskLoopStage.EXECUTION, TaskLoopStage.TEST, TaskLoopStage.JUDGE, TaskLoopStage.COMPLETE,
        ]
        for current, nxt in zip(path, path[1:]):
            assert_stage_transition_allowed(current, nxt)  # ne doit jamais lever

    def test_judge_can_route_to_analyze_on_failure(self) -> None:
        assert_stage_transition_allowed(TaskLoopStage.JUDGE, TaskLoopStage.ANALYZE)

    def test_the_full_retry_path_returns_to_plan_never_to_objective(self) -> None:
        assert_stage_transition_allowed(TaskLoopStage.ANALYZE, TaskLoopStage.STRATEGY_UPDATE)
        assert_stage_transition_allowed(TaskLoopStage.STRATEGY_UPDATE, TaskLoopStage.RETRY)
        assert_stage_transition_allowed(TaskLoopStage.RETRY, TaskLoopStage.PLAN)
        with self.assertRaises(InvalidTaskLoopTransition):
            assert_stage_transition_allowed(TaskLoopStage.RETRY, TaskLoopStage.OBJECTIVE)

    def test_strategy_update_can_reach_all_three_recovery_actions(self) -> None:
        for target in (TaskLoopStage.RETRY, TaskLoopStage.REASSIGN, TaskLoopStage.BLOCK):
            assert_stage_transition_allowed(TaskLoopStage.STRATEGY_UPDATE, target)

    def test_terminal_stages_allow_no_further_transition(self) -> None:
        for terminal in (TaskLoopStage.COMPLETE, TaskLoopStage.BLOCK):
            self.assertTrue(terminal.is_terminal)
            with self.assertRaises(InvalidTaskLoopTransition):
                assert_stage_transition_allowed(terminal, TaskLoopStage.OBJECTIVE)

    def test_skipping_a_stage_is_illegal(self) -> None:
        with self.assertRaises(InvalidTaskLoopTransition):
            assert_stage_transition_allowed(TaskLoopStage.OBJECTIVE, TaskLoopStage.EXECUTION)

    def test_every_stage_is_reachable_from_objective(self) -> None:
        """Aucune étape orpheline (jamais atteignable) dans le graphe."""
        reachable = {TaskLoopStage.OBJECTIVE}
        frontier = [TaskLoopStage.OBJECTIVE]
        while frontier:
            current = frontier.pop()
            for nxt in TaskLoopStage:
                try:
                    assert_stage_transition_allowed(current, nxt)
                except InvalidTaskLoopTransition:
                    continue
                if nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)
        self.assertEqual(reachable, set(TaskLoopStage))


def _criteria(**overrides) -> TaskLoopStopCriteria:
    defaults = dict(
        max_attempts=3, max_wall_clock_s=60.0, resource_budget_description="1 GPU, 4 Go RAM",
        success_criteria=lambda r: r == "ok", failure_criteria=lambda r: r == "fatal",
    )
    defaults.update(overrides)
    return TaskLoopStopCriteria(**defaults)


class TestTaskLoopStopCriteriaValidation(unittest.TestCase):
    def test_rejects_non_positive_max_attempts(self) -> None:
        with self.assertRaises(ValueError):
            _criteria(max_attempts=0)

    def test_rejects_non_positive_wall_clock(self) -> None:
        with self.assertRaises(ValueError):
            _criteria(max_wall_clock_s=0)

    def test_rejects_empty_resource_budget_description(self) -> None:
        with self.assertRaises(ValueError):
            _criteria(resource_budget_description="   ")


class TestCheckStop(unittest.TestCase):
    def test_continues_when_nothing_triggers(self) -> None:
        self.assertIsNone(check_stop(attempt_number=1, elapsed_s=1.0, criteria=_criteria(), last_result="pending"))

    def test_success_criteria_stops(self) -> None:
        self.assertIs(
            check_stop(attempt_number=1, elapsed_s=1.0, criteria=_criteria(), last_result="ok"),
            StopReason.SUCCESS,
        )

    def test_failure_criteria_stops(self) -> None:
        self.assertIs(
            check_stop(attempt_number=1, elapsed_s=1.0, criteria=_criteria(), last_result="fatal"),
            StopReason.FAILURE_CRITERIA_MET,
        )

    def test_max_attempts_stops(self) -> None:
        self.assertIs(
            check_stop(attempt_number=3, elapsed_s=1.0, criteria=_criteria(max_attempts=3), last_result="pending"),
            StopReason.MAX_ATTEMPTS_REACHED,
        )

    def test_wall_clock_stops(self) -> None:
        self.assertIs(
            check_stop(
                attempt_number=1, elapsed_s=61.0, criteria=_criteria(max_wall_clock_s=60.0), last_result="pending",
            ),
            StopReason.WALL_CLOCK_EXCEEDED,
        )

    def test_cancellation_always_wins_even_with_budget_remaining(self) -> None:
        token = CancellationToken()
        token.request_cancel()
        reason = check_stop(
            attempt_number=1, elapsed_s=1.0, criteria=_criteria(max_attempts=100), cancel_token=token,
            last_result="pending",
        )
        self.assertIs(reason, StopReason.CANCELLED)

    def test_cancellation_beats_a_simultaneous_success(self) -> None:
        """Ordre de priorité explicite — voir la docstring de `check_stop`."""
        token = CancellationToken()
        token.request_cancel()
        reason = check_stop(
            attempt_number=1, elapsed_s=1.0, criteria=_criteria(), cancel_token=token, last_result="ok",
        )
        self.assertIs(reason, StopReason.CANCELLED)

    def test_infrastructure_failure_stops_when_enabled(self) -> None:
        reason = check_stop(
            attempt_number=1, elapsed_s=1.0, criteria=_criteria(), last_result="pending",
            infrastructure_failure=True,
        )
        self.assertIs(reason, StopReason.INFRASTRUCTURE_FAILURE)

    def test_infrastructure_failure_can_be_explicitly_disabled(self) -> None:
        reason = check_stop(
            attempt_number=1, elapsed_s=1.0,
            criteria=_criteria(stop_on_infrastructure_failure=False), last_result="pending",
            infrastructure_failure=True,
        )
        self.assertIsNone(reason)

    def test_provider_unavailable_stops_when_enabled(self) -> None:
        reason = check_stop(
            attempt_number=1, elapsed_s=1.0, criteria=_criteria(), last_result="pending",
            provider_unavailable=True,
        )
        self.assertIs(reason, StopReason.PROVIDER_UNAVAILABLE)

    def test_never_loops_forever_regardless_of_criteria_callables(self) -> None:
        """Même si `success_criteria`/`failure_criteria` ne matchent
        jamais, le budget de tentatives finit toujours par arrêter."""
        never = _criteria(success_criteria=lambda r: False, failure_criteria=lambda r: False, max_attempts=5)
        for attempt in range(1, 5):
            self.assertIsNone(check_stop(attempt_number=attempt, elapsed_s=0.0, criteria=never, last_result="x"))
        self.assertIs(
            check_stop(attempt_number=5, elapsed_s=0.0, criteria=never, last_result="x"),
            StopReason.MAX_ATTEMPTS_REACHED,
        )


if __name__ == "__main__":
    unittest.main()
