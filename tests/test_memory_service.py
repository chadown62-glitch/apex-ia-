"""Tests de MemoryService — cycle de vie complet, SQLite en mémoire.

Même patron que tests/test_missions_service.py : ce qui est vérifié est
l'application de PolicyEngine existant (aucune action ajoutée par l'étape 9),
l'immuabilité (une révision est une nouvelle ligne, une promotion ne touche
que la visibilité), les relations SUPPORTS/CONTRADICTS (références
uniquement vers des MemoryRecord existants), et le rejet honnête des cas
invalides.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import EventRepository
from apex.errors import PolicyDenied
from apex.memory.model import MemoryKind, MemoryVisibility, RelationType, VerificationStatus
from apex.memory.service import MemoryService
from apex.policy.model import Subject
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestWrite(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MemoryService(self.factory)

    def test_research_can_write_a_memory_record(self) -> None:
        record = self.service.write(
            kind=MemoryKind.SEMANTIC, content="La Terre est ronde", source="https://example.org/geo",
            created_by=Subject.RESEARCH, confidence=0.9, verification_status=VerificationStatus.VERIFIED,
        )
        self.assertEqual(record.content, "La Terre est ronde")
        self.assertIs(record.visibility, MemoryVisibility.OWN)

    def test_learning_developer_and_experiment_can_also_write(self) -> None:
        for subject in (Subject.LEARNING, Subject.DEVELOPER, Subject.EXPERIMENT):
            with self.subTest(subject=subject):
                record = self.service.write(
                    kind=MemoryKind.PROCEDURAL, content="x", source="s", created_by=subject,
                )
                self.assertEqual(record.created_by, subject.value)

    def test_core_and_human_cannot_write_own_memory(self) -> None:
        """WRITE_OWN_MEMORY n'est accordée qu'aux clones concernés — ni CORE
        ni HUMAN ne l'ont dans l'allow-list par défaut (apex.policy.engine)."""
        for subject in (Subject.CORE, Subject.HUMAN):
            with self.subTest(subject=subject):
                with self.assertRaises(PolicyDenied):
                    self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=subject)

    def test_critic_cannot_write_memory(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.CRITIC)

    def test_empty_content_rejected_before_any_write(self) -> None:
        with self.assertRaises(ValueError):
            self.service.write(kind=MemoryKind.SEMANTIC, content=" ", source="s", created_by=Subject.RESEARCH)

    def test_write_persists_the_record(self) -> None:
        record = self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH)
        fetched = self.service.get(record.id)
        assert fetched is not None
        self.assertEqual(fetched.content, "x")

    def test_write_records_an_event_and_an_audit_entry(self) -> None:
        from apex.db.repositories import AuditLogRepository

        record = self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH)
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
            audits = AuditLogRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("memory_id") == record.id]
        self.assertIn("memory.written", kinds)
        actions = [a.action for a in audits if a.target == record.id]
        self.assertIn("memory.written", actions)

    def test_external_provenance_is_stored(self) -> None:
        record = self.service.write(
            kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH,
            external_provenance=True, external_source="gpt-oss:20b",
        )
        self.assertTrue(record.external_provenance)
        self.assertEqual(record.external_source, "gpt-oss:20b")


class TestRevise(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MemoryService(self.factory)
        self.original = self.service.write(
            kind=MemoryKind.SEMANTIC, content="La Terre est plate", source="s",
            created_by=Subject.RESEARCH, confidence=0.1, verification_status=VerificationStatus.DISPUTED,
        )

    def test_revise_creates_a_new_row_never_mutates_the_original(self) -> None:
        revised = self.service.revise(
            self.original.id, requested_by=Subject.RESEARCH, content="La Terre est ronde",
            source="s2", confidence=0.95, verification_status=VerificationStatus.VERIFIED,
        )
        self.assertNotEqual(revised.id, self.original.id)
        self.assertEqual(revised.supersedes_id, self.original.id)
        # L'original reste inchangé, consultable tel quel — historique conservé.
        original_still_there = self.service.get(self.original.id)
        assert original_still_there is not None
        self.assertEqual(original_still_there.content, "La Terre est plate")
        self.assertIs(original_still_there.verification_status, VerificationStatus.DISPUTED)

    def test_revising_an_already_revised_record_is_rejected(self) -> None:
        self.service.revise(
            self.original.id, requested_by=Subject.RESEARCH, content="v2", source="s", confidence=0.5,
        )
        with self.assertRaises(ValueError):
            self.service.revise(
                self.original.id, requested_by=Subject.RESEARCH, content="v3", source="s", confidence=0.5,
            )

    def test_revising_a_nonexistent_record_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            self.service.revise(
                "does-not-exist", requested_by=Subject.RESEARCH, content="x", source="s", confidence=0.5,
            )

    def test_revision_carries_forward_the_domain(self) -> None:
        with_domain = self.service.write(
            kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH, domain="geo",
        )
        revised = self.service.revise(
            with_domain.id, requested_by=Subject.RESEARCH, content="y", source="s", confidence=0.5,
        )
        self.assertEqual(revised.domain, "geo")


class TestPromoteToShared(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MemoryService(self.factory)
        self.record = self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH)

    def test_denied_to_everyone_by_default(self) -> None:
        """§2.5 : promouvoir un souvenir vers la mémoire partagée reste un
        acte explicite, jamais un droit par défaut — vérifié ici à travers
        MemoryService, pas seulement au niveau de PolicyEngine isolé."""
        for subject in Subject:
            with self.subTest(subject=subject):
                with self.assertRaises(PolicyDenied):
                    self.service.promote_to_shared(self.record.id, requested_by=subject)

    def test_promotion_only_changes_visibility_never_content(self) -> None:
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action

        custom_allowlist = {
            Subject.RESEARCH: frozenset({Action.WRITE_OWN_MEMORY, Action.PROMOTE_MEMORY_TO_SHARED}),
            # CORE doit garder WRITE_AUDIT_LOG : MemoryService écrit
            # toujours l'audit pour son propre compte (même motif que
            # MissionService/AgentRuntime), y compris avec une allow-list
            # personnalisée construite pour ce test.
            Subject.CORE: frozenset({Action.WRITE_AUDIT_LOG}),
        }
        service = MemoryService(self.factory, policy=PolicyEngine(allowlist=custom_allowlist))
        record = service.write(kind=MemoryKind.SEMANTIC, content="contenu original", source="s", created_by=Subject.RESEARCH)

        promoted = service.promote_to_shared(record.id, requested_by=Subject.RESEARCH)
        self.assertIs(promoted.visibility, MemoryVisibility.SHARED)
        self.assertEqual(promoted.content, "contenu original")
        self.assertEqual(promoted.id, record.id)

    def test_double_promotion_is_rejected(self) -> None:
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action

        custom_allowlist = {
            Subject.RESEARCH: frozenset({Action.WRITE_OWN_MEMORY, Action.PROMOTE_MEMORY_TO_SHARED}),
            # CORE doit garder WRITE_AUDIT_LOG : MemoryService écrit
            # toujours l'audit pour son propre compte (même motif que
            # MissionService/AgentRuntime), y compris avec une allow-list
            # personnalisée construite pour ce test.
            Subject.CORE: frozenset({Action.WRITE_AUDIT_LOG}),
        }
        service = MemoryService(self.factory, policy=PolicyEngine(allowlist=custom_allowlist))
        record = service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH)
        service.promote_to_shared(record.id, requested_by=Subject.RESEARCH)
        with self.assertRaises(ValueError):
            service.promote_to_shared(record.id, requested_by=Subject.RESEARCH)


class TestReadShared(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MemoryService(self.factory)

    def test_own_records_never_appear_in_read_shared(self) -> None:
        self.service.write(kind=MemoryKind.SEMANTIC, content="x", source="s", created_by=Subject.RESEARCH)
        shared = self.service.read_shared(requested_by=Subject.HUMAN)
        self.assertEqual(shared, [])

    def test_developer_cannot_read_shared_memory(self) -> None:
        """READ_SHARED_MEMORY n'est pas dans l'allow-list de DEVELOPER."""
        with self.assertRaises(PolicyDenied):
            self.service.read_shared(requested_by=Subject.DEVELOPER)

    def test_core_and_human_can_read_shared_memory(self) -> None:
        for subject in (Subject.CORE, Subject.HUMAN):
            with self.subTest(subject=subject):
                self.assertEqual(self.service.read_shared(requested_by=subject), [])


class TestRelate(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MemoryService(self.factory)
        self.a = self.service.write(kind=MemoryKind.SEMANTIC, content="La Terre est ronde", source="s1", created_by=Subject.RESEARCH, verification_status=VerificationStatus.VERIFIED)
        self.b = self.service.write(kind=MemoryKind.SEMANTIC, content="La Terre est plate", source="s2", created_by=Subject.RESEARCH, verification_status=VerificationStatus.FALSE)

    def test_contradicts_relation_between_two_existing_records(self) -> None:
        rel = self.service.relate(
            source_id=self.b.id, target_id=self.a.id, relation=RelationType.CONTRADICTS,
            provenance="cross-check manuel", requested_by=Subject.RESEARCH,
        )
        self.assertEqual(rel.source_id, self.b.id)
        self.assertEqual(rel.target_id, self.a.id)
        self.assertIs(rel.relation, RelationType.CONTRADICTS)

    def test_supports_relation_between_two_existing_records(self) -> None:
        c = self.service.write(kind=MemoryKind.SEMANTIC, content="La Terre est un sphéroïde", source="s3", created_by=Subject.RESEARCH)
        rel = self.service.relate(
            source_id=c.id, target_id=self.a.id, relation=RelationType.SUPPORTS,
            provenance="reformulation cohérente", requested_by=Subject.RESEARCH,
        )
        self.assertIs(rel.relation, RelationType.SUPPORTS)

    def test_relation_to_a_nonexistent_target_is_rejected(self) -> None:
        with self.assertRaises(LookupError):
            self.service.relate(
                source_id=self.a.id, target_id="does-not-exist", relation=RelationType.SUPPORTS,
                provenance="p", requested_by=Subject.RESEARCH,
            )

    def test_relation_from_a_nonexistent_source_is_rejected(self) -> None:
        with self.assertRaises(LookupError):
            self.service.relate(
                source_id="does-not-exist", target_id=self.a.id, relation=RelationType.SUPPORTS,
                provenance="p", requested_by=Subject.RESEARCH,
            )

    def test_relation_creation_requires_write_own_memory(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.relate(
                source_id=self.a.id, target_id=self.b.id, relation=RelationType.CONTRADICTS,
                provenance="p", requested_by=Subject.HUMAN,
            )

    def test_list_relations_for_finds_both_directions(self) -> None:
        rel = self.service.relate(
            source_id=self.b.id, target_id=self.a.id, relation=RelationType.CONTRADICTS,
            provenance="p", requested_by=Subject.RESEARCH,
        )
        for record_id in (self.a.id, self.b.id):
            with self.subTest(record_id=record_id):
                relations = self.service.list_relations_for(record_id)
                self.assertIn(rel.id, [r.id for r in relations])


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Même patron que tests/test_missions_service.py::TestImportableWithoutSqlalchemy
    — nécessaire avant toute déclaration `module="apex.memory.service"` dans
    le registre (déjà faite, étape 9)."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys
        import unittest.mock

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("apex.memory")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.memory.service as fresh_service

                self.assertTrue(hasattr(fresh_service, "MemoryService"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.memory") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMemoryServiceAgainstRealPostgres(unittest.TestCase):
    """Le cycle complet write -> revise -> relate -> promote_to_shared contre
    une vraie base PostgreSQL migrée (pas seulement les permissions de rôle,
    déjà couvertes par tests/test_db_security.py::TestMemoryRoleIsWriteRestricted) —
    même motif que tests/test_scheduler_service.py::TestAtomicClaimAgainstRealPostgres."""

    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        import os

        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def test_full_cycle_against_real_postgres(self) -> None:
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action

        factory = self._factory()
        allowlist = {
            Subject.RESEARCH: frozenset({Action.WRITE_OWN_MEMORY}),
            Subject.HUMAN: frozenset({Action.READ_SHARED_MEMORY, Action.PROMOTE_MEMORY_TO_SHARED}),
            Subject.CORE: frozenset({Action.WRITE_AUDIT_LOG, Action.READ_SHARED_MEMORY}),
        }
        service = MemoryService(factory, policy=PolicyEngine(allowlist=allowlist))

        false_claim = service.write(
            kind=MemoryKind.SEMANTIC, content="La Terre est plate", source="https://example.org/flat",
            created_by=Subject.RESEARCH, confidence=0.05, verification_status=VerificationStatus.FALSE,
        )
        true_claim = service.write(
            kind=MemoryKind.SEMANTIC, content="La Terre est ronde", source="https://example.org/geo",
            created_by=Subject.RESEARCH, confidence=0.95, verification_status=VerificationStatus.VERIFIED,
        )
        service.relate(
            source_id=false_claim.id, target_id=true_claim.id, relation=RelationType.CONTRADICTS,
            provenance="cross-check réel", requested_by=Subject.RESEARCH,
        )
        revised = service.revise(
            true_claim.id, requested_by=Subject.RESEARCH, content="La Terre est un sphéroïde aplati",
            source="https://example.org/geo2", confidence=0.99,
            verification_status=VerificationStatus.VERIFIED,
        )
        promoted = service.promote_to_shared(revised.id, requested_by=Subject.HUMAN)

        self.assertIs(promoted.visibility, MemoryVisibility.SHARED)
        shared = service.read_shared(requested_by=Subject.HUMAN)
        self.assertIn(revised.id, [r.id for r in shared])

        relations = service.list_relations_for(true_claim.id)
        self.assertEqual(len(relations), 1)
        self.assertIs(relations[0].relation, RelationType.CONTRADICTS)

        # L'original révisé reste lisible, jamais supprimé — historique conservé.
        original = service.get(true_claim.id)
        assert original is not None
        self.assertEqual(original.content, "La Terre est ronde")
        self.assertEqual(revised.supersedes_id, true_claim.id)


if __name__ == "__main__":
    unittest.main()
