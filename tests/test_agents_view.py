"""Tests de apex.agents.view — contrat de présentation pour la future API."""

from __future__ import annotations

import unittest

from datetime import datetime, timezone

from apex.agents.model import Clone
from apex.agents.view import DataStatus, clone_history, to_view
from apex.db.models import EventRow
from apex.policy.engine import PolicyEngine
from apex.policy.model import Subject


def a_clone(**overrides) -> Clone:
    defaults = dict(
        clone_type=Subject.RESEARCH, genome_ref="sha256:abc", spawned_by="core",
        pool_id="local-gpu", isolation_level="docker_hardened",
    )
    defaults.update(overrides)
    clone_type = defaults.pop("clone_type")
    return Clone.new(clone_type, **defaults)


class TestToView(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PolicyEngine()

    def test_view_carries_identity_and_state(self) -> None:
        clone = a_clone()
        view = to_view(clone, viewer=Subject.HUMAN, policy=self.policy)
        self.assertEqual(view.clone_id, clone.clone_id)
        self.assertEqual(view.type, "research")
        self.assertEqual(view.state, "spawning")

    def test_ram_and_gpu_are_unknown_never_zero(self) -> None:
        """RAM/GPU ne sont mesurées par rien tant que SandboxProvider n'existe pas."""
        view = to_view(a_clone(), viewer=Subject.HUMAN, policy=self.policy)
        self.assertIsNone(view.ram_mb.value)
        self.assertEqual(view.ram_mb.status, DataStatus.UNKNOWN)
        self.assertIsNone(view.gpu.value)
        self.assertEqual(view.gpu.status, DataStatus.UNKNOWN)

    def test_elapsed_is_real_when_provided(self) -> None:
        view = to_view(a_clone(), viewer=Subject.HUMAN, policy=self.policy, elapsed_s=1.234)
        self.assertEqual(view.elapsed_s.value, 1.234)
        self.assertEqual(view.elapsed_s.status, DataStatus.REAL)

    def test_elapsed_is_unknown_when_not_provided(self) -> None:
        view = to_view(a_clone(), viewer=Subject.HUMAN, policy=self.policy)
        self.assertEqual(view.elapsed_s.status, DataStatus.UNKNOWN)

    def test_isolation_level_unknown_is_flagged_as_such(self) -> None:
        view = to_view(a_clone(isolation_level="unknown"), viewer=Subject.HUMAN, policy=self.policy)
        self.assertEqual(view.isolation_level.status, DataStatus.UNKNOWN)

    def test_isolation_level_measured_is_flagged_real(self) -> None:
        view = to_view(a_clone(isolation_level="docker_hardened"), viewer=Subject.HUMAN, policy=self.policy)
        self.assertEqual(view.isolation_level.status, DataStatus.REAL)

    def test_human_viewer_can_see_terminate_action(self) -> None:
        view = to_view(a_clone(), viewer=Subject.HUMAN, policy=self.policy)
        self.assertIn("terminate_clone", view.available_actions)

    def test_research_clone_viewer_cannot_terminate(self) -> None:
        """Un clone n'a pas TERMINATE_CLONE dans l'allow-list par défaut —
        même en tant que « viewer », l'action ne doit pas apparaître."""
        view = to_view(a_clone(), viewer=Subject.RESEARCH, policy=self.policy)
        self.assertNotIn("terminate_clone", view.available_actions)

    def test_to_dict_is_json_serialisable_shape(self) -> None:
        import json

        view = to_view(a_clone(), viewer=Subject.HUMAN, policy=self.policy, elapsed_s=0.5)
        json.dumps(view.to_dict())  # ne doit pas lever


class TestCloneHistory(unittest.TestCase):
    def test_filters_events_by_clone_id(self) -> None:
        now = datetime.now(timezone.utc)
        events = [
            EventRow(id=1, ts=now, level="INFO", logger="x", msg="clone.spawned", context={"clone_id": "a"}),
            EventRow(id=2, ts=now, level="INFO", logger="x", msg="clone.spawned", context={"clone_id": "b"}),
        ]
        history = clone_history(events, "a")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
