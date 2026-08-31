"""Tests de GenomeStoreService — cycle de vie complet, SQLite en mémoire +
vrai ArtifactStore (répertoire temporaire réel, pas de mock du stockage).

Même patron que tests/test_skills_service.py : PolicyEngine (aucun clone ne
peut proposer/promouvoir sa propre version — CORE/HUMAIN proposent, HUMAIN
seul décide), déterminisme du hash de contenu, la généalogie
(parent_version_id/parent_version_id_b), et get_current() dérivé des
décisions elles-mêmes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from sqlalchemy import create_engine

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import AuditLogRepository, EventRepository
from apex.errors import PolicyDenied
from apex.genome.model import GenomeVersionOrigin, GenomeVersionStatus
from apex.genome.service import GenomeStoreService, canonical_json_bytes
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class _TempStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="apex_genome_test_")
        self.factory = sqlite_session_factory()
        self.artifacts = LocalFilesystemArtifactStore(root=os.path.join(self.tmpdir, "artifacts"))
        self.service = GenomeStoreService(self.factory, artifact_store=self.artifacts)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestCanonicalJson(unittest.TestCase):
    def test_key_order_does_not_affect_the_hash(self) -> None:
        a = canonical_json_bytes({"b": 1, "a": 2})
        b = canonical_json_bytes({"a": 2, "b": 1})
        self.assertEqual(a, b)


class TestPropose(_TempStoreTestCase):
    def test_human_can_propose(self) -> None:
        version = self.service.propose(content={"levels": 3}, created_by=Subject.HUMAN)
        self.assertEqual(version.status, GenomeVersionStatus.PROPOSED)
        self.assertTrue(version.content_hash.startswith("sha256:"))

    def test_core_can_propose(self) -> None:
        version = self.service.propose(content={"levels": 3}, created_by=Subject.CORE)
        self.assertEqual(version.status, GenomeVersionStatus.PROPOSED)

    def test_a_clone_cannot_propose(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.propose(content={"levels": 3}, created_by=Subject.RESEARCH)

    def test_content_is_retrievable_after_proposal(self) -> None:
        version = self.service.propose(content={"levels": 3, "note": "x"}, created_by=Subject.HUMAN)
        self.assertEqual(self.service.get_content(version.id), {"levels": 3, "note": "x"})

    def test_identical_content_yields_the_same_hash(self) -> None:
        v1 = self.service.propose(content={"a": 1}, created_by=Subject.HUMAN)
        v2 = self.service.propose(content={"a": 1}, created_by=Subject.HUMAN)
        self.assertEqual(v1.content_hash, v2.content_hash)
        self.assertNotEqual(v1.id, v2.id)

    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.propose(content={}, created_by=Subject.HUMAN)

    def test_nonexistent_parent_rejected(self) -> None:
        with self.assertRaises(LookupError):
            self.service.propose(content={"a": 1}, created_by=Subject.HUMAN, parent_version_id="does-not-exist")

    def test_propose_writes_event_and_audit(self) -> None:
        version = self.service.propose(content={"a": 1}, created_by=Subject.HUMAN)
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
            audits = AuditLogRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("genome_version_id") == version.id]
        self.assertIn("genome_version.proposed", kinds)
        self.assertIn(version.id, [a.target for a in audits])


class TestPromoteAndReject(_TempStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.version = self.service.propose(content={"levels": 3}, created_by=Subject.HUMAN)

    def test_human_can_promote(self) -> None:
        promoted = self.service.promote(self.version.id, requested_by=Subject.HUMAN)
        self.assertEqual(promoted.status, GenomeVersionStatus.PROMOTED)

    def test_core_cannot_promote(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.promote(self.version.id, requested_by=Subject.CORE)

    def test_a_clone_cannot_promote(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.promote(self.version.id, requested_by=Subject.RESEARCH)

    def test_a_clone_cannot_reject(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.reject(self.version.id, requested_by=Subject.RESEARCH)

    def test_double_decision_rejected(self) -> None:
        self.service.promote(self.version.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.promote(self.version.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.reject(self.version.id, requested_by=Subject.HUMAN)


class TestCurrentVersion(_TempStoreTestCase):
    def test_no_current_before_any_promotion(self) -> None:
        self.service.propose(content={"levels": 3}, created_by=Subject.HUMAN)
        self.assertIsNone(self.service.get_current())

    def test_current_is_the_most_recently_promoted(self) -> None:
        v1 = self.service.propose(content={"levels": 1}, created_by=Subject.HUMAN)
        self.service.promote(v1.id, requested_by=Subject.HUMAN)
        v2 = self.service.propose(content={"levels": 2}, created_by=Subject.HUMAN)
        self.service.promote(v2.id, requested_by=Subject.HUMAN)
        current = self.service.get_current()
        self.assertEqual(current.id, v2.id)
        self.assertEqual(self.service.get_current_content(), {"levels": 2})

    def test_a_rejected_version_never_becomes_current(self) -> None:
        v1 = self.service.propose(content={"levels": 1}, created_by=Subject.HUMAN)
        self.service.promote(v1.id, requested_by=Subject.HUMAN)
        v2 = self.service.propose(content={"levels": 2}, created_by=Subject.HUMAN)
        self.service.reject(v2.id, requested_by=Subject.HUMAN)
        self.assertEqual(self.service.get_current().id, v1.id)


class TestGenealogy(_TempStoreTestCase):
    def test_mutation_records_a_single_parent(self) -> None:
        parent = self.service.propose(content={"levels": 1}, created_by=Subject.HUMAN)
        self.service.promote(parent.id, requested_by=Subject.HUMAN)
        child = self.service.propose(
            content={"levels": 2}, created_by=Subject.CORE, origin=GenomeVersionOrigin.MUTATION,
            parent_version_id=parent.id,
        )
        self.assertEqual(child.parent_version_id, parent.id)
        self.assertIsNone(child.parent_version_id_b)
        self.assertEqual(child.origin, GenomeVersionOrigin.MUTATION)

    def test_crossover_records_two_parents(self) -> None:
        parent_a = self.service.propose(content={"levels": 1}, created_by=Subject.HUMAN)
        parent_b = self.service.propose(content={"levels": 2}, created_by=Subject.HUMAN)
        self.service.promote(parent_a.id, requested_by=Subject.HUMAN)
        self.service.promote(parent_b.id, requested_by=Subject.HUMAN)
        child = self.service.propose(
            content={"levels": 3}, created_by=Subject.CORE, origin=GenomeVersionOrigin.CROSSOVER,
            parent_version_id=parent_a.id, parent_version_id_b=parent_b.id,
        )
        self.assertEqual(child.parent_version_id, parent_a.id)
        self.assertEqual(child.parent_version_id_b, parent_b.id)


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression : le registre déclare module="apex.genome.service"
    (module_importable(), ADR-0007)."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys
        import unittest.mock

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("apex.genome")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.genome.service as fresh_service

                self.assertTrue(hasattr(fresh_service, "GenomeStoreService"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.genome") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()
