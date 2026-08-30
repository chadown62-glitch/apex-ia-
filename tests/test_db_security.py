"""Sécurité SQL réelle — rôles, GRANT/REVOKE, audit_log en écriture seule.

Ces garanties ne sont PAS testables via SQLite (tests/test_db_models.py le
documente déjà) : SQLite n'a pas de rôles ni de GRANT/REVOKE. Chaque test ici
se connecte RÉELLEMENT avec le rôle applicatif concerné (jamais le
superutilisateur, qui contournerait tout) et vérifie que PostgreSQL refuse
l'opération interdite — pas seulement que le DDL de la migration la déclare.

SKIP explicite si PostgreSQL réel n'est pas joignable : voir
tests/db_test_support.py et docs/PHASE_2_REPORT.md.
"""

from __future__ import annotations

import unittest

from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAuditLogInsertOnly(unittest.TestCase):
    """Critère de validation §5.2 du brief : DELETE sur audit_log doit échouer."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_audit_can_insert(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (ts, actor, action, target, details) "
                "VALUES (now(), 'governor', 'promote', 'genome:x', '{}'::jsonb)"
            )

    def test_apex_audit_cannot_delete(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log")

    def test_apex_audit_cannot_update(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE audit_log SET actor = 'x'")

    def test_apex_audit_cannot_truncate(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("TRUNCATE audit_log")

    def test_apex_core_can_read_audit_log_but_not_write(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM audit_log")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAgentRoleIsWriteRestricted(unittest.TestCase):
    """apex_agent journalise des événements mais ne modifie jamais compute_pools
    ni les événements déjà écrits — anti-contamination minimale (§6 PHASE_1_REPORT)."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_agent_can_insert_events(self) -> None:
        with support.role_connection("apex_agent") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (ts, level, logger, msg, context) "
                "VALUES (now(), 'INFO', 'apex.agent', 'run', '{}'::jsonb)"
            )

    def test_apex_agent_cannot_update_events(self) -> None:
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE events SET level = 'DEBUG'")

    def test_apex_agent_cannot_write_compute_pools(self) -> None:
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO compute_pools (pool_id, location, trust_level, capabilities) "
                        "VALUES ('rogue', 'local', 'trusted', '[]'::jsonb)"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestReadonlyRoleIsReadOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_readonly_can_select(self) -> None:
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM compute_pools")
            cur.execute("SELECT count(*) FROM events")
            cur.execute("SELECT count(*) FROM audit_log")

    def test_apex_readonly_cannot_insert_anywhere(self) -> None:
        import psycopg

        for statement in (
            "INSERT INTO compute_pools (pool_id, location, trust_level, capabilities) "
            "VALUES ('x', 'local', 'trusted', '[]'::jsonb)",
            "INSERT INTO events (ts, level, logger, msg, context) "
            "VALUES (now(), 'INFO', 'x', 'x', '{}'::jsonb)",
            "INSERT INTO audit_log (ts, actor, action, target, details) "
            "VALUES (now(), 'x', 'x', NULL, '{}'::jsonb)",
        ):
            with support.role_connection("apex_readonly") as conn:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with conn.cursor() as cur:
                        cur.execute(statement)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMissionRoleIsWriteRestricted(unittest.TestCase):
    """apex_core est le seul rôle qui écrit `missions` — aucun rôle de clone
    (apex_agent/apex_eval/apex_audit) n'y a accès : cohérent avec
    PolicyEngine (aucun Subject de clone n'a CREATE/CANCEL/VALIDATE/
    REJECT_MISSION dans son allow-list)."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_missions(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO missions (id, title, description, specialty, objective, priority, "
                "state, created_by, created_at) VALUES "
                "('m1', 't', 'd', 'research', 'o', 'normal', 'draft', 'human', now())"
            )
            cur.execute("UPDATE missions SET state = 'queued' WHERE id = 'm1'")

    def test_apex_core_cannot_delete_missions(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM missions")

    def test_apex_agent_cannot_write_missions(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `missions` — pas même en lecture."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO missions (id, title, description, specialty, objective, "
                        "priority, state, created_by, created_at) VALUES "
                        "('rogue', 't', 'd', 'research', 'o', 'normal', 'draft', 'research', now())"
                    )

    def test_apex_readonly_can_select_but_not_insert_missions(self) -> None:
        import psycopg

        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM missions")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO missions (id, title, description, specialty, objective, "
                        "priority, state, created_by, created_at) VALUES "
                        "('x', 't', 'd', 'research', 'o', 'normal', 'draft', 'human', now())"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMemoryRoleIsWriteRestricted(unittest.TestCase):
    """apex_core est le seul rôle qui écrit `memory_records`/`memory_relations`
    — aucun rôle de clone n'y a accès direct, cohérent avec PolicyEngine
    (un clone n'écrit jamais sa propre ligne, seul MemoryService le fait
    pour son compte). DELETE/TRUNCATE refusés même à apex_core : aucune
    ligne mémoire n'est jamais supprimée, seule une révision (nouvelle
    ligne) ou une promotion de visibilité (UPDATE ciblé) existe."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_memory_records(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem1', 'semantic', 'x', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute("UPDATE memory_records SET visibility = 'shared' WHERE id = 'mem1'")

    def test_apex_core_cannot_delete_memory_records(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memory_records")

    def test_apex_core_can_insert_memory_relations_but_not_delete(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem2', 'semantic', 'y', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem3', 'semantic', 'z', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute(
                "INSERT INTO memory_relations (id, source_id, target_id, relation, "
                "provenance, created_by) VALUES "
                "('rel1', 'mem2', 'mem3', 'contradicts', 'cross-check', 'research')"
            )
        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memory_relations")

    def test_apex_agent_cannot_write_memory_records(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `memory_records` —
        pas même en lecture : seul MemoryService écrit pour son compte."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO memory_records (id, kind, content, source, created_by, "
                        "confidence, verification_status, visibility) VALUES "
                        "('rogue', 'semantic', 'x', 'x', 'research', 0.5, 'uncertain', 'own')"
                    )

    def test_apex_readonly_can_select_but_not_insert_memory_records(self) -> None:
        import psycopg

        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memory_records")
            cur.execute("SELECT count(*) FROM memory_relations")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO memory_records (id, kind, content, source, created_by, "
                        "confidence, verification_status, visibility) VALUES "
                        "('x', 'semantic', 'x', 'x', 'human', 0.5, 'uncertain', 'own')"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestHeldoutSplitSeparation(unittest.TestCase):
    """Critère de validation §5.3 du brief : le rôle worker ne doit pas lire
    le split HELDOUT.

    DÉLIBÉRÉMENT SAUTÉ, pas simulé. Ce test exigerait une table de tâches
    avec une colonne « split » — c'est-à-dire une partie du schéma de
    TaskBank, qui reste PLANNED (phase 5, apex/interfaces/registry.py).
    Créer cette table maintenant pour faire passer ce seul test reviendrait
    exactement à ce qu'interdit ADR-0007 : déclarer une structure avant que
    l'abstraction qui la possède existe. Ce critère est donc reporté à la
    phase qui implémente TaskBank, où il pourra être testé contre le vrai
    schéma plutôt qu'un schéma fictif construit pour la circonstance.
    """

    @unittest.skip(
        "Reporté à la phase 5 (TaskBank) — voir docstring de la classe et "
        "docs/PHASE_2_REPORT.md, section critères différés."
    )
    def test_worker_role_cannot_read_heldout_split(self) -> None:
        raise NotImplementedError


if __name__ == "__main__":
    unittest.main()
