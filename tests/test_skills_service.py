"""Tests de SkillRegistryService — cycle de vie complet, SQLite en mémoire.

Même patron que tests/test_missions_service.py : PolicyEngine (aucun clone ne
peut proposer/promouvoir sa propre compétence — seuls CORE/HUMAN proposent,
seul HUMAN décide), validation (spécialité, prérequis, preuve), le graphe de
prérequis (existence + cycle, réutilise apex.missions.dependencies), la
promotion bloquée par des prérequis non acquis, la décision unique, et la
généalogie par révision (supersedes_id).
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import AuditLogRepository, EventRepository
from apex.errors import PolicyDenied
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.missions.dependencies import DependencyCycle
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus
from apex.skills.model import SkillStatus
from apex.skills.service import SkillRegistryService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def persist_eval_run(factory) -> EvalRun:
    run = EvalRun.new(
        task_id="t1", clone_id="c1", method=EvalMethod.PRACTICE, oracle_verdict=OracleVerdict.PASS,
        oracle_detail=None, sandbox_status=ExecutionStatus.SUCCESS, exit_code=0,
        elapsed_s=0.1, facts={}, created_by="core",
    )
    EvalRunRepository(factory).create(run)
    return run


class TestPropose(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = SkillRegistryService(self.factory)

    def test_human_can_propose_a_skill(self) -> None:
        skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        self.assertEqual(skill.status, SkillStatus.PROPOSED)

    def test_core_can_propose_a_skill(self) -> None:
        skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.CORE,
        )
        self.assertEqual(skill.status, SkillStatus.PROPOSED)

    def test_a_clone_cannot_propose_a_skill(self) -> None:
        """§8 anti-auto-illusion : un clone ne peut jamais se créditer lui-même."""
        with self.assertRaises(PolicyDenied):
            self.service.propose(
                name="n", specialty="research", description="d", created_by=Subject.RESEARCH,
            )

    def test_propose_persists_the_skill(self) -> None:
        skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        fetched = self.service.get(skill.id)
        assert fetched is not None
        self.assertEqual(fetched.name, "n")

    def test_propose_writes_event_and_audit(self) -> None:
        skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
            audits = AuditLogRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("skill_id") == skill.id]
        self.assertIn("skill.proposed", kinds)
        self.assertIn(skill.id, [a.target for a in audits])

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(name="  ", specialty="research", description="d", created_by=Subject.HUMAN)

    def test_empty_description_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(name="n", specialty="research", description=" ", created_by=Subject.HUMAN)

    def test_unknown_specialty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(
                name="n", specialty="does-not-exist", description="d", created_by=Subject.HUMAN,
            )

    def test_planned_specialty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(
                name="n", specialty="code-intelligence", description="d", created_by=Subject.HUMAN,
            )

    def test_confidence_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(
                name="n", specialty="research", description="d", created_by=Subject.HUMAN, confidence=1.5,
            )

    def test_nonexistent_prerequisite_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(
                name="n", specialty="research", description="d", created_by=Subject.HUMAN,
                prerequisite_skill_ids=("does-not-exist",),
            )

    def test_a_manufactured_cycle_in_the_persisted_graph_is_rejected(self) -> None:
        """`prerequisite_skill_ids` est immuable après création (aucune
        méthode ne le mute) : un cycle est donc impossible par construction
        en usage normal — même raisonnement que `MissionRow.depends_on`
        (voir `tests/test_missions_service.py::TestDependsOn::
        test_a_manufactured_cycle_in_the_persisted_graph_is_rejected`).
        Ce test vérifie que la seconde ligne de défense explicite
        (`apex.missions.dependencies.detect_cycle`, réutilisée telle quelle)
        est bien câblée dans `SkillRegistryService.propose` — pas
        seulement testée isolément dans `test_missions_dependencies.py`."""
        a = self.service.propose(name="a", specialty="research", description="d", created_by=Subject.HUMAN)
        b = self.service.propose(
            name="b", specialty="research", description="d", created_by=Subject.HUMAN,
            prerequisite_skill_ids=(a.id,),
        )
        # Contournement délibéré de l'immuabilité normale (aucune méthode
        # publique ne permet ça) — uniquement pour prouver que detect_cycle()
        # est réellement invoqué à la proposition suivante.
        from apex.db.engine import session_scope
        from apex.db.models import SkillRow

        with session_scope(self.factory) as session:
            row = session.get(SkillRow, a.id)
            assert row is not None
            row.prerequisite_skill_ids = [b.id]

        with self.assertRaises(DependencyCycle):
            self.service.propose(
                name="c", specialty="research", description="d", created_by=Subject.HUMAN,
                prerequisite_skill_ids=(a.id,),
            )

    def test_evidence_run_id_must_exist(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(
                name="n", specialty="research", description="d", created_by=Subject.HUMAN,
                evidence_run_ids=("does-not-exist",),
            )

    def test_valid_evidence_is_accepted_and_persisted(self) -> None:
        run = persist_eval_run(self.factory)
        skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
            evidence_run_ids=(run.id,),
        )
        self.assertEqual(skill.evidence_run_ids, (run.id,))


class TestPromote(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = SkillRegistryService(self.factory)
        self.skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )

    def test_human_can_promote(self) -> None:
        promoted = self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        self.assertEqual(promoted.status, SkillStatus.ACQUIRED)
        self.assertEqual(promoted.decided_by, "human")
        self.assertIsNotNone(promoted.decided_at)

    def test_core_cannot_promote(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.promote(self.skill.id, requested_by=Subject.CORE)

    def test_a_clone_cannot_promote(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.promote(self.skill.id, requested_by=Subject.RESEARCH)

    def test_double_promotion_rejected(self) -> None:
        self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.promote(self.skill.id, requested_by=Subject.HUMAN)

    def test_promotion_blocked_by_unmet_prerequisite(self) -> None:
        child = self.service.propose(
            name="child", specialty="research", description="d", created_by=Subject.HUMAN,
            prerequisite_skill_ids=(self.skill.id,),
        )
        with self.assertRaises(ValueError):
            self.service.promote(child.id, requested_by=Subject.HUMAN)

    def test_promotion_succeeds_once_prerequisite_is_acquired(self) -> None:
        child = self.service.propose(
            name="child", specialty="research", description="d", created_by=Subject.HUMAN,
            prerequisite_skill_ids=(self.skill.id,),
        )
        self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        promoted_child = self.service.promote(child.id, requested_by=Subject.HUMAN)
        self.assertEqual(promoted_child.status, SkillStatus.ACQUIRED)

    def test_promotion_writes_event_and_audit(self) -> None:
        self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("skill_id") == self.skill.id]
        self.assertIn("skill.promoted", kinds)


class TestReject(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = SkillRegistryService(self.factory)
        self.skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )

    def test_human_can_reject(self) -> None:
        rejected = self.service.reject(self.skill.id, requested_by=Subject.HUMAN, reason="preuve insuffisante")
        self.assertEqual(rejected.status, SkillStatus.REJECTED)

    def test_core_cannot_reject(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.reject(self.skill.id, requested_by=Subject.CORE)

    def test_reject_then_promote_rejected(self) -> None:
        self.service.reject(self.skill.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.promote(self.skill.id, requested_by=Subject.HUMAN)


class TestRevise(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = SkillRegistryService(self.factory)
        self.skill = self.service.propose(
            name="n", specialty="research", description="d", created_by=Subject.HUMAN,
        )

    def test_cannot_revise_a_still_proposed_skill(self) -> None:
        with self.assertRaises(ValueError):
            self.service.revise(self.skill.id, name="n2", description="d2", created_by=Subject.HUMAN)

    def test_cannot_revise_a_rejected_skill(self) -> None:
        self.service.reject(self.skill.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.revise(self.skill.id, name="n2", description="d2", created_by=Subject.HUMAN)

    def test_revising_an_acquired_skill_creates_a_new_proposed_entry(self) -> None:
        self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        revised = self.service.revise(self.skill.id, name="n2", description="d2", created_by=Subject.CORE)
        self.assertEqual(revised.status, SkillStatus.PROPOSED)
        self.assertEqual(revised.supersedes_id, self.skill.id)
        # L'original n'est PAS muté — jamais un historique réécrit.
        original = self.service.get(self.skill.id)
        self.assertEqual(original.status, SkillStatus.ACQUIRED)
        self.assertEqual(original.name, "n")

    def test_double_revision_rejected(self) -> None:
        self.service.promote(self.skill.id, requested_by=Subject.HUMAN)
        self.service.revise(self.skill.id, name="n2", description="d2", created_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.revise(self.skill.id, name="n3", description="d3", created_by=Subject.HUMAN)


class TestReadMethods(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = SkillRegistryService(self.factory)

    def test_list_acquired_excludes_proposed_and_rejected(self) -> None:
        acquired = self.service.propose(name="a", specialty="research", description="d", created_by=Subject.HUMAN)
        proposed = self.service.propose(name="p", specialty="research", description="d", created_by=Subject.HUMAN)
        rejected = self.service.propose(name="r", specialty="research", description="d", created_by=Subject.HUMAN)
        self.service.promote(acquired.id, requested_by=Subject.HUMAN)
        self.service.reject(rejected.id, requested_by=Subject.HUMAN)

        result_ids = [s.id for s in self.service.list_acquired("research")]
        self.assertIn(acquired.id, result_ids)
        self.assertNotIn(proposed.id, result_ids)
        self.assertNotIn(rejected.id, result_ids)


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression : le registre déclare module="apex.skills.service"
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
            or name.startswith("apex.skills")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.skills.service as fresh_service

                self.assertTrue(hasattr(fresh_service, "SkillRegistryService"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.skills") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()
