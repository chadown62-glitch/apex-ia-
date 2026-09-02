"""Tests d'`AgentTemplateService` — SQLite en mémoire, même patron que
`tests/test_projects_service.py`. Vérifie explicitement que créer/lister/
supprimer un template n'implique JAMAIS un spawn réel de clone (aucun appel
à `AgentRuntime`/`SPAWN_CLONE` nulle part dans ce service).
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agent_templates.model import AgentRole, AgentTemplateStatus
from apex.agent_templates.service import AgentTemplateService
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestAgentTemplateLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = AgentTemplateService(self.factory)

    def test_human_can_create_a_template(self) -> None:
        template = self.service.create(
            name="Codeur Python", role=AgentRole.CODER, description="d", created_by=Subject.HUMAN,
        )
        self.assertEqual(template.role, AgentRole.CODER)

    def test_a_new_template_is_never_executable(self) -> None:
        """Une DÉFINITION n'est jamais confondue avec une instance réelle
        (`apex.agents`, `AgentRuntime`) — toujours `DEFINED`, jamais un
        autre statut qui laisserait croire à un spawn réel."""
        template = self.service.create(
            name="n", role=AgentRole.RESEARCHER, description="d", created_by=Subject.HUMAN,
        )
        self.assertEqual(template.status, AgentTemplateStatus.DEFINED)

    def test_a_clone_cannot_create_a_template(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.create(name="x", role=AgentRole.TESTER, description="", created_by=Subject.DEVELOPER)

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(name="  ", role=AgentRole.ANALYST, description="", created_by=Subject.HUMAN)

    def test_tools_are_stored_as_declared_never_verified(self) -> None:
        template = self.service.create(
            name="n", role=AgentRole.SECURITY, description="d", created_by=Subject.HUMAN,
            tools=("linter", "test-runner"),
        )
        self.assertEqual(template.tools, ("linter", "test-runner"))

    def test_model_role_is_stored_as_a_convention_never_validated(self) -> None:
        template = self.service.create(
            name="n", role=AgentRole.EVALUATOR, description="d", created_by=Subject.HUMAN,
            model_role="does-not-exist-in-any-config",
        )
        self.assertEqual(template.model_role, "does-not-exist-in-any-config")

    def test_created_template_is_listed(self) -> None:
        template = self.service.create(
            name="n", role=AgentRole.DESIGNER, description="d", created_by=Subject.HUMAN,
        )
        listed = self.service.list_all()
        self.assertIn(template.id, [t.id for t in listed])

    def test_human_can_delete_a_template(self) -> None:
        template = self.service.create(
            name="n", role=AgentRole.CODER, description="d", created_by=Subject.HUMAN,
        )
        deleted = self.service.delete(template.id, requested_by=Subject.HUMAN)
        self.assertTrue(deleted)
        self.assertIsNone(self.service.get(template.id))

    def test_a_clone_cannot_delete_a_template(self) -> None:
        template = self.service.create(
            name="n", role=AgentRole.CODER, description="d", created_by=Subject.HUMAN,
        )
        with self.assertRaises(PolicyDenied):
            self.service.delete(template.id, requested_by=Subject.CRITIC)

    def test_delete_unknown_template_returns_false(self) -> None:
        self.assertFalse(self.service.delete("does-not-exist", requested_by=Subject.HUMAN))


if __name__ == "__main__":
    unittest.main()
