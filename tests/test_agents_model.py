"""Tests de la machine à états d'un clone — apex.agents.model."""

from __future__ import annotations

import unittest

from apex.agents.model import CLONE_SUBJECTS, Clone, CloneState, InvalidTransition
from apex.policy.model import Subject


class TestCloneSubjects(unittest.TestCase):
    def test_core_and_human_are_never_clone_types(self) -> None:
        self.assertNotIn(Subject.CORE, CLONE_SUBJECTS)
        self.assertNotIn(Subject.HUMAN, CLONE_SUBJECTS)

    def test_exactly_nine_clone_types(self) -> None:
        """Les neuf types de docs/COGNITIVE_ARCHITECTURE.md §2.2."""
        self.assertEqual(len(CLONE_SUBJECTS), 9)


class TestCloneCreation(unittest.TestCase):
    def test_new_clone_starts_spawning(self) -> None:
        clone = Clone.new(
            Subject.RESEARCH, genome_ref="sha256:abc", spawned_by="core",
            pool_id="local-gpu", isolation_level="docker_hardened",
        )
        self.assertEqual(clone.state, CloneState.SPAWNING)
        self.assertEqual(clone.retries_used, 0)
        self.assertIsNone(clone.terminated_at)

    def test_each_clone_gets_a_distinct_id(self) -> None:
        a = Clone.new(Subject.RESEARCH, genome_ref="x", spawned_by="core", pool_id="p", isolation_level="unknown")
        b = Clone.new(Subject.RESEARCH, genome_ref="x", spawned_by="core", pool_id="p", isolation_level="unknown")
        self.assertNotEqual(a.clone_id, b.clone_id)


class TestTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.clone = Clone.new(
            Subject.RESEARCH, genome_ref="sha256:abc", spawned_by="core",
            pool_id="local-gpu", isolation_level="unknown",
        )

    def test_full_success_path(self) -> None:
        for state in (
            CloneState.READY, CloneState.WORKING, CloneState.REPORTING, CloneState.TERMINATED,
        ):
            self.clone.transition(state)
        self.assertEqual(self.clone.state, CloneState.TERMINATED)

    def test_crash_and_recovery_path(self) -> None:
        self.clone.transition(CloneState.READY)
        self.clone.transition(CloneState.WORKING)
        self.clone.transition(CloneState.CRASHED)
        self.clone.transition(CloneState.RECOVERING)
        self.clone.transition(CloneState.SPAWNING)
        self.clone.transition(CloneState.READY)
        self.assertEqual(self.clone.state, CloneState.READY)

    def test_crash_to_failed_when_no_recovery(self) -> None:
        self.clone.transition(CloneState.READY)
        self.clone.transition(CloneState.WORKING)
        self.clone.transition(CloneState.CRASHED)
        self.clone.transition(CloneState.FAILED)
        self.assertTrue(self.clone.state.is_terminal)

    def test_illegal_transition_raises(self) -> None:
        """SPAWNING -> WORKING n'existe pas : il faut passer par READY."""
        with self.assertRaises(InvalidTransition):
            self.clone.transition(CloneState.WORKING)

    def test_terminal_states_accept_no_further_transition(self) -> None:
        self.clone.transition(CloneState.READY)
        self.clone.transition(CloneState.TERMINATING)
        self.clone.transition(CloneState.TERMINATED)
        with self.assertRaises(InvalidTransition):
            self.clone.transition(CloneState.WORKING)

    def test_terminated_and_failed_are_terminal(self) -> None:
        self.assertTrue(CloneState.TERMINATED.is_terminal)
        self.assertTrue(CloneState.FAILED.is_terminal)
        self.assertFalse(CloneState.WORKING.is_terminal)
        self.assertFalse(CloneState.CRASHED.is_terminal)

    def test_ready_can_be_terminated_directly(self) -> None:
        """Un clone READY qui n'a jamais travaillé peut être annulé."""
        self.clone.transition(CloneState.READY)
        self.clone.transition(CloneState.TERMINATING)
        self.clone.transition(CloneState.TERMINATED)
        self.assertEqual(self.clone.state, CloneState.TERMINATED)


if __name__ == "__main__":
    unittest.main()
