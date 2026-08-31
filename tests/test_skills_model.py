"""Tests du modèle de domaine d'une Skill — apex.skills.model, aucune DB."""

from __future__ import annotations

import unittest

from apex.skills.model import Skill, SkillStatus


class TestSkillCreation(unittest.TestCase):
    def test_new_skill_starts_proposed(self) -> None:
        skill = Skill.new(name="n", specialty="research", description="d", created_by="human")
        self.assertEqual(skill.status, SkillStatus.PROPOSED)
        self.assertIsNone(skill.decided_by)
        self.assertIsNone(skill.decided_at)
        self.assertIsNone(skill.supersedes_id)

    def test_each_skill_gets_a_distinct_id(self) -> None:
        a = Skill.new(name="a", specialty="research", description="d", created_by="human")
        b = Skill.new(name="b", specialty="research", description="d", created_by="human")
        self.assertNotEqual(a.id, b.id)

    def test_prerequisites_and_evidence_default_to_empty(self) -> None:
        skill = Skill.new(name="n", specialty="research", description="d", created_by="human")
        self.assertEqual(skill.prerequisite_skill_ids, ())
        self.assertEqual(skill.evidence_run_ids, ())

    def test_prerequisites_and_evidence_are_stored_as_tuples(self) -> None:
        skill = Skill.new(
            name="n", specialty="research", description="d", created_by="human",
            prerequisite_skill_ids=("a", "b"), evidence_run_ids=("r1",),
        )
        self.assertEqual(skill.prerequisite_skill_ids, ("a", "b"))
        self.assertEqual(skill.evidence_run_ids, ("r1",))

    def test_supersedes_id_can_be_set_for_a_revision(self) -> None:
        skill = Skill.new(
            name="n", specialty="research", description="d", created_by="human", supersedes_id="old-id",
        )
        self.assertEqual(skill.supersedes_id, "old-id")


if __name__ == "__main__":
    unittest.main()
