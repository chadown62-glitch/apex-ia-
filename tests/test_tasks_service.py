"""Tests de TaskBankService — cycle de vie, SQLite en mémoire.

Même patron que tests/test_missions_service.py : ce qui est vérifié est
l'application de PolicyEngine (aucun clone ne peut créer sa propre tâche —
seul HUMAN le peut), la validation (spécialité inconnue/PLANNED, commande
vide), la persistance et la journalisation (events/audit_log), et le split
HELDOUT/TRAIN.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import AuditLogRepository, EventRepository
from apex.errors import PolicyDenied
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestCreate(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = TaskBankService(self.factory)

    def test_human_can_create_a_task(self) -> None:
        task = self.service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        self.assertEqual(task.split, TaskSplit.TRAIN)

    def test_create_persists_the_task(self) -> None:
        task = self.service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        fetched = self.service.get(task.id)
        assert fetched is not None
        self.assertEqual(fetched.title, "t")
        self.assertEqual(fetched.command, ("true",))

    def test_create_writes_created_event_and_audit(self) -> None:
        task = self.service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
            audits = AuditLogRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("task_id") == task.id]
        self.assertIn("task.created", kinds)
        targets = [a.target for a in audits]
        self.assertIn(task.id, targets)

    def test_a_clone_cannot_create_a_task(self) -> None:
        """Contournement trivial du split HELDOUT sinon : un clone ne doit
        jamais pouvoir écrire sa propre tâche d'évaluation."""
        with self.assertRaises(PolicyDenied):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
                command=("true",), created_by=Subject.RESEARCH,
            )

    def test_core_cannot_create_a_task_either(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
                command=("true",), created_by=Subject.CORE,
            )

    def test_empty_title_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="research", title="   ", description="d",
                command=("true",), created_by=Subject.HUMAN,
            )

    def test_empty_command_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
                command=(), created_by=Subject.HUMAN,
            )

    def test_command_with_empty_part_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
                command=("true", ""), created_by=Subject.HUMAN,
            )

    def test_unknown_specialty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="does-not-exist", title="t", description="d",
                command=("true",), created_by=Subject.HUMAN,
            )

    def test_planned_specialty_rejected(self) -> None:
        """« code-intelligence » est PLANNED (apex.specialties.registry) —
        aucune tâche ne peut lui être assignée pour l'instant, même motif
        que MissionService."""
        with self.assertRaises(ValueError):
            self.service.create(
                split=TaskSplit.TRAIN, specialty="code-intelligence", title="t", description="d",
                command=("true",), created_by=Subject.HUMAN,
            )


class TestReadBySplit(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = TaskBankService(self.factory)
        self.train_task = self.service.create(
            split=TaskSplit.TRAIN, specialty="research", title="train-1", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        self.heldout_task = self.service.create(
            split=TaskSplit.HELDOUT, specialty="research", title="heldout-1", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )

    def test_list_by_split_train_excludes_heldout(self) -> None:
        train_tasks = self.service.list_by_split(TaskSplit.TRAIN)
        ids = [t.id for t in train_tasks]
        self.assertIn(self.train_task.id, ids)
        self.assertNotIn(self.heldout_task.id, ids)

    def test_list_by_split_heldout_excludes_train(self) -> None:
        heldout_tasks = self.service.list_by_split(TaskSplit.HELDOUT)
        ids = [t.id for t in heldout_tasks]
        self.assertIn(self.heldout_task.id, ids)
        self.assertNotIn(self.train_task.id, ids)

    def test_list_all_includes_both(self) -> None:
        ids = [t.id for t in self.service.list_all()]
        self.assertIn(self.train_task.id, ids)
        self.assertIn(self.heldout_task.id, ids)


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression : le registre déclare module="apex.tasks.service"
    (module_importable(), ADR-0007). Même patron que
    tests/test_missions_service.py::TestImportableWithoutSqlalchemy."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys
        import unittest.mock

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("apex.tasks")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.tasks.service as fresh_service

                self.assertTrue(hasattr(fresh_service, "TaskBankService"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.tasks") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()
