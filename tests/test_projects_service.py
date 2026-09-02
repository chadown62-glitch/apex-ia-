"""Tests de `ProjectService` — SQLite en mémoire, même patron que
`tests/test_conversations_service.py`.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.policy.model import Subject
from apex.projects.service import ProjectService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestProjectLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = ProjectService(self.factory)

    def test_human_can_create_a_project(self) -> None:
        project = self.service.create(name="APEX v0.1", description="première interface", created_by=Subject.HUMAN)
        self.assertEqual(project.name, "APEX v0.1")

    def test_a_clone_cannot_create_a_project(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(name="x", description="", created_by=Subject.RESEARCH)

    def test_core_cannot_create_a_project(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(name="x", description="", created_by=Subject.CORE)

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(name="   ", description="", created_by=Subject.HUMAN)

    def test_created_project_is_retrievable(self) -> None:
        project = self.service.create(name="p", description="d", created_by=Subject.HUMAN)
        fetched = self.service.get(project.id)
        assert fetched is not None
        self.assertEqual(fetched.name, "p")

    def test_created_project_is_listed(self) -> None:
        project = self.service.create(name="p", description="d", created_by=Subject.HUMAN)
        listed = self.service.list_all()
        self.assertIn(project.id, [p.id for p in listed])

    def test_unknown_project_returns_none(self) -> None:
        self.assertIsNone(self.service.get("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
