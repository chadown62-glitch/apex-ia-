"""Migrations Alembic contre PostgreSQL réel — aller ET retour.

SKIP explicite (jamais un PASS déguisé) si PostgreSQL n'est pas réellement
joignable avec les identifiants attendus. Voir tests/db_test_support.py et
docs/PHASE_2_REPORT.md pour ce qui reste à valider sur votre machine WSL2
si ces tests sautent ici.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect, text

from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


def _engine():
    import os

    host, port = support._pg_host_port()
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{host}:{port}/{support.TEST_DB_NAME}"
    )
    return create_engine(url, future=True)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMigrationUpAndDown(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()

    def tearDown(self) -> None:
        # Ordre obligatoire : les rôles portent des GRANT dans la base de test ;
        # DROP ROLE échoue tant que ces objets existent. Supprimer la base
        # d'abord retire les GRANT qu'elle contient.
        support.drop_test_database()
        support.drop_application_roles()

    def test_upgrade_creates_expected_tables_and_partitions(self) -> None:
        support.run_upgrade()
        engine = _engine()
        tables = set(inspect(engine).get_table_names())
        self.assertTrue(
            {"compute_pools", "events", "audit_log", "agents", "missions", "alembic_version"} <= tables
        )
        partitions = {t for t in tables if t.startswith("events_")}
        self.assertIn("events_default", partitions)
        self.assertGreaterEqual(len(partitions), 3, "au moins 2 mois à venir + défaut")

    def test_missions_table_grants_match_policy_engine(self) -> None:
        """Seuls apex_core (écriture) et apex_readonly (lecture) touchent
        `missions` — cohérent avec PolicyEngine : CREATE/CANCEL/VALIDATE/
        REJECT_MISSION ne sont accordées qu'à HUMAN/CORE, jamais à un clone."""
        support.run_upgrade()
        engine = _engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'missions' AND grantee != 'apex' ORDER BY grantee, privilege_type"
                )
            ).all()
        by_grantee: dict[str, set[str]] = {}
        for grantee, privilege in rows:
            by_grantee.setdefault(grantee, set()).add(privilege)
        self.assertEqual(by_grantee.get("apex_core"), {"SELECT", "INSERT", "UPDATE"})
        self.assertEqual(by_grantee.get("apex_readonly"), {"SELECT"})
        self.assertNotIn("apex_agent", by_grantee)
        self.assertNotIn("apex_eval", by_grantee)
        self.assertNotIn("apex_audit", by_grantee)

    def test_agents_table_grants_match_policy_engine(self) -> None:
        """Seuls apex_core (écriture) et apex_readonly (lecture) touchent
        `agents` — cohérent avec PolicyEngine : SPAWN_CLONE/TERMINATE_CLONE
        ne sont accordées qu'à CORE/HUMAN, jamais à un clone."""
        support.run_upgrade()
        engine = _engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'agents' AND grantee != 'apex' ORDER BY grantee, privilege_type"
                )
            ).all()
        by_grantee: dict[str, set[str]] = {}
        for grantee, privilege in rows:
            by_grantee.setdefault(grantee, set()).add(privilege)
        self.assertEqual(by_grantee.get("apex_core"), {"SELECT", "INSERT", "UPDATE"})
        self.assertEqual(by_grantee.get("apex_readonly"), {"SELECT"})
        self.assertNotIn("apex_agent", by_grantee)
        self.assertNotIn("apex_eval", by_grantee)
        self.assertNotIn("apex_audit", by_grantee)

    def test_upgrade_creates_all_application_roles(self) -> None:
        support.run_upgrade()
        engine = _engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'apex_%' ORDER BY rolname")
            ).all()
        roles = {r[0] for r in rows}
        self.assertEqual(
            roles, {"apex_core", "apex_agent", "apex_eval", "apex_audit", "apex_readonly"}
        )

    def test_downgrade_removes_tables_and_roles(self) -> None:
        support.run_upgrade()
        support.run_downgrade()
        engine = _engine()
        tables = set(inspect(engine).get_table_names())
        # alembic_version est la table de suivi d'Alembic lui-même : elle
        # survit intentionnellement à "downgrade base" (vide, prête pour un
        # futur "upgrade head") — ce n'est pas une table applicative.
        self.assertEqual(
            tables - {"alembic_version"}, set(), f"tables applicatives restantes : {tables}"
        )
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'apex_%'")
            ).all()
        self.assertEqual(rows, [], "des rôles applicatifs survivent au downgrade")

    def test_round_trip_twice_is_stable(self) -> None:
        """Un aller-retour ne doit pas laisser le schéma dans un état différent
        du premier aller — sinon la migration n'est pas vraiment réversible."""
        support.run_upgrade()
        support.run_downgrade()
        support.run_upgrade()
        engine = _engine()
        tables = set(inspect(engine).get_table_names())
        self.assertTrue({"compute_pools", "events", "audit_log"} <= tables)


if __name__ == "__main__":
    unittest.main()
