"""Tests de la machine à états d'une Mission — apex.missions.states/model."""

from __future__ import annotations

import unittest

from apex.missions.model import Mission, MissionPriority
from apex.missions.states import InvalidMissionTransition, MissionState


class TestMissionCreation(unittest.TestCase):
    def test_new_mission_starts_draft(self) -> None:
        mission = Mission.new(
            title="Explorer X", description="d", specialty="research",
            objective="trouver une hypothèse", created_by="human",
        )
        self.assertEqual(mission.state, MissionState.DRAFT)
        self.assertIsNone(mission.validation_status)
        self.assertIsNone(mission.completed_at)
        self.assertEqual(mission.priority, MissionPriority.NORMAL)

    def test_each_mission_gets_a_distinct_id(self) -> None:
        a = Mission.new(title="a", description="", specialty="research", objective="o", created_by="human")
        b = Mission.new(title="b", description="", specialty="research", objective="o", created_by="human")
        self.assertNotEqual(a.id, b.id)


class TestTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.mission = Mission.new(
            title="t", description="d", specialty="research", objective="o", created_by="human",
        )

    def test_full_success_path(self) -> None:
        for state in (MissionState.QUEUED, MissionState.RUNNING, MissionState.REVIEW, MissionState.COMPLETED):
            self.mission.transition(state)
        self.assertEqual(self.mission.state, MissionState.COMPLETED)
        self.assertTrue(self.mission.state.is_terminal)

    def test_rejected_path(self) -> None:
        for state in (MissionState.QUEUED, MissionState.RUNNING, MissionState.REVIEW, MissionState.FAILED):
            self.mission.transition(state)
        self.assertTrue(self.mission.state.is_terminal)

    def test_revision_requested_returns_to_draft(self) -> None:
        for state in (MissionState.QUEUED, MissionState.RUNNING, MissionState.REVIEW, MissionState.DRAFT):
            self.mission.transition(state)
        self.assertEqual(self.mission.state, MissionState.DRAFT)
        self.assertFalse(self.mission.state.is_terminal)

    def test_cancel_from_draft(self) -> None:
        self.mission.transition(MissionState.CANCELLED)
        self.assertTrue(self.mission.state.is_terminal)

    def test_cancel_from_queued(self) -> None:
        self.mission.transition(MissionState.QUEUED)
        self.mission.transition(MissionState.CANCELLED)
        self.assertEqual(self.mission.state, MissionState.CANCELLED)

    def test_cancel_from_running(self) -> None:
        self.mission.transition(MissionState.QUEUED)
        self.mission.transition(MissionState.RUNNING)
        self.mission.transition(MissionState.CANCELLED)
        self.assertEqual(self.mission.state, MissionState.CANCELLED)

    def test_draft_cannot_jump_to_running(self) -> None:
        """La chaîne ne doit jamais être raccourcie : DRAFT doit passer par QUEUED."""
        with self.assertRaises(InvalidMissionTransition):
            self.mission.transition(MissionState.RUNNING)

    def test_draft_cannot_jump_to_review_or_completed(self) -> None:
        for target in (MissionState.REVIEW, MissionState.COMPLETED, MissionState.FAILED):
            with self.subTest(target=target):
                fresh = Mission.new(title="t", description="", specialty="research", objective="o", created_by="human")
                with self.assertRaises(InvalidMissionTransition):
                    fresh.transition(target)

    def test_queued_cannot_go_directly_to_review(self) -> None:
        self.mission.transition(MissionState.QUEUED)
        with self.assertRaises(InvalidMissionTransition):
            self.mission.transition(MissionState.REVIEW)

    def test_terminal_states_accept_no_further_transition(self) -> None:
        self.mission.transition(MissionState.CANCELLED)
        for target in (MissionState.QUEUED, MissionState.RUNNING, MissionState.DRAFT):
            with self.subTest(target=target):
                with self.assertRaises(InvalidMissionTransition):
                    self.mission.transition(target)

    def test_completed_failed_cancelled_are_terminal(self) -> None:
        self.assertTrue(MissionState.COMPLETED.is_terminal)
        self.assertTrue(MissionState.FAILED.is_terminal)
        self.assertTrue(MissionState.CANCELLED.is_terminal)
        self.assertFalse(MissionState.DRAFT.is_terminal)
        self.assertFalse(MissionState.QUEUED.is_terminal)
        self.assertFalse(MissionState.RUNNING.is_terminal)
        self.assertFalse(MissionState.REVIEW.is_terminal)


if __name__ == "__main__":
    unittest.main()
