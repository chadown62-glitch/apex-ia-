"""Tests du modèle de domaine d'une Task — apex.tasks.model, aucune DB."""

from __future__ import annotations

import unittest

from apex.tasks.model import Task, TaskSplit


class TestTaskCreation(unittest.TestCase):
    def test_new_task_carries_the_given_split_and_specialty(self) -> None:
        task = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "pass"), created_by="human",
        )
        self.assertEqual(task.split, TaskSplit.TRAIN)
        self.assertEqual(task.specialty, "research")
        self.assertEqual(task.command, ("python3", "-c", "pass"))

    def test_each_task_gets_a_distinct_id(self) -> None:
        a = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="a", description="d",
            command=("true",), created_by="human",
        )
        b = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="b", description="d",
            command=("true",), created_by="human",
        )
        self.assertNotEqual(a.id, b.id)

    def test_heldout_split_is_a_distinct_value(self) -> None:
        task = Task.new(
            split=TaskSplit.HELDOUT, specialty="research", title="t", description="d",
            command=("true",), created_by="human",
        )
        self.assertEqual(task.split, TaskSplit.HELDOUT)
        self.assertNotEqual(TaskSplit.HELDOUT, TaskSplit.TRAIN)

    def test_command_is_stored_as_a_tuple_never_a_shell_string(self) -> None:
        """Même contrat que SandboxSpec.command — un argv, jamais une chaîne
        shell (voir apex.tasks.model.Task, apex.sandbox.model.SandboxSpec)."""
        task = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=["python3", "-c", "pass"], created_by="human",
        )
        self.assertIsInstance(task.command, tuple)

    def test_dataset_ref_defaults_to_none(self) -> None:
        task = Task.new(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by="human",
        )
        self.assertIsNone(task.dataset_ref)

    def test_no_method_field_exists_on_task(self) -> None:
        """Décision explicite (docs/COGNITIVE_ARCHITECTURE.md §5) : `method`
        est une propriété du run (`apex.evalrunner.model.EvalRun`), jamais
        de la tâche — sinon impossible de comparer plusieurs méthodes sur
        la même tâche."""
        fields = Task.__dataclass_fields__
        self.assertNotIn("method", fields)


if __name__ == "__main__":
    unittest.main()
