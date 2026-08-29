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
