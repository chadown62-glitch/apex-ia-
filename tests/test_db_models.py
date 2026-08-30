"""Tests des modèles, repositories, engine et EventSink DB — phase 2.

SQLite en mémoire valide la logique générique (mapping, contraintes Python,
rédaction des secrets). Ce n'est PAS une preuve de conformité PostgreSQL :
rôles SQL, GRANT/REVOKE, partitionnement natif, pgvector et le comportement
exact des migrations restent hors de portée de ce fichier — voir
`tests/test_db_security.py` et `tests/test_db_migrations.py`, qui sautent
explicitement quand PostgreSQL réel n'est pas disponible plutôt que de
prétendre valider ce que SQLite ne peut pas garantir.
"""

from __future__ import annotations

import unittest
import unittest.mock
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from apex.config.loader import parse_config
from apex.config.schema import PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, database_url_from_config, session_scope
from apex.db.event_sink import DatabaseEventSink
from apex.db.models import AuditLogRow, Base, ComputePoolRow, EventRow
from apex.db.repositories import AuditLogRepository, ComputePoolRepository, EventRepository
from apex.errors import ConfigError
from tests.test_config import config as config_dict


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestSchema(unittest.TestCase):
    def test_all_tables_are_created(self) -> None:
        """Inclusion, pas égalité stricte : Base.metadata grandit légitimement
        à chaque nouvelle abstraction persistée (ex. agents, AgentRuntime) —
        figer un compte exact casserait ce test à chaque extension honnête du
        schéma plutôt que de vérifier ce qu'il est censé vérifier : que les
        tables fondamentales de la phase 2 sont toujours là."""
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        self.assertTrue({"compute_pools", "events", "audit_log"} <= tables)


class TestComputePoolRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.cfg = parse_config(config_dict())

    def test_upsert_then_get_round_trips(self) -> None:
        pool = self.cfg.pools["local-gpu"]
        with session_scope(self.factory) as session:
            ComputePoolRepository(session).upsert(pool)
        with session_scope(self.factory) as session:
            row = ComputePoolRepository(session).get("local-gpu")
            assert row is not None
            self.assertEqual(row.trust_level, TrustLevel.TRUSTED.value)
            self.assertEqual(row.location, PoolLocation.LOCAL.value)
            self.assertEqual(row.cpu_cores, 12)

    def test_upsert_is_idempotent(self) -> None:
        pool = self.cfg.pools["local-gpu"]
        with session_scope(self.factory) as session:
            ComputePoolRepository(session).upsert(pool)
        with session_scope(self.factory) as session:
            ComputePoolRepository(session).upsert(pool)
        with session_scope(self.factory) as session:
            self.assertEqual(len(ComputePoolRepository(session).list_all()), 1)


class TestEventRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()

    def test_add_and_recent(self) -> None:
        with session_scope(self.factory) as session:
            EventRepository(session).add(
                ts=datetime.now(timezone.utc), level="INFO", logger="apex.test",
                msg="hello", context={"task_id": "t1"},
            )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].msg, "hello")
            self.assertEqual(events[0].context, {"task_id": "t1"})


class TestAuditLogRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()

    def test_record_and_recent(self) -> None:
        with session_scope(self.factory) as session:
            AuditLogRepository(session).record(
                ts=datetime.now(timezone.utc), actor="governor", action="promote",
                target="genome:abc123", details={"reason": "benchmark"},
            )
        with session_scope(self.factory) as session:
            rows = AuditLogRepository(session).recent()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].action, "promote")

    def test_repository_exposes_no_update_or_delete_method(self) -> None:
        """Garde Python : en plus de la révocation SQL (migration), aucune
        méthode de modification n'existe même côté Core."""
        public_methods = {
            name for name in dir(AuditLogRepository) if not name.startswith("_")
        }
        self.assertEqual(public_methods, {"record", "recent"})


class TestDatabaseEventSink(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.sink = DatabaseEventSink(self.factory)

    def test_emit_persists_event(self) -> None:
        ok = self.sink.emit(level="INFO", logger="apex.cli", msg="démarrage", context={})
        self.assertTrue(ok)
        with session_scope(self.factory) as session:
            self.assertEqual(len(EventRepository(session).recent()), 1)

    def test_emit_redacts_secrets_before_persisting(self) -> None:
        """Un secret passé par erreur en contexte ne doit pas atteindre la base,
        exactement la garantie déjà testée pour le formateur JSON."""
        self.sink.emit(
            level="INFO", logger="apex.test", msg="config chargée",
            context={"database_password": "hunter2", "note": "bearer sk-ABCDEFGHIJKLMNOPQR"},
        )
        with session_scope(self.factory) as session:
            event = EventRepository(session).recent()[0]
            self.assertEqual(event.context["database_password"], "***redacted***")
            self.assertNotIn("sk-ABCDEFGHIJKLMNOPQR", event.context["note"])

    def test_emit_never_raises_on_broken_sink(self) -> None:
        broken_factory = sessionmaker(bind=create_engine("sqlite:////nonexistent/dir/db.sqlite"))
        sink = DatabaseEventSink(broken_factory)
        ok = sink.emit(level="INFO", logger="apex.test", msg="x", context={})
        self.assertFalse(ok)

    def test_health_ok_against_reachable_database(self) -> None:
        health = self.sink.health()
        self.assertEqual(health.status.value, "ok")

    def test_health_failed_never_raises_on_broken_sink(self) -> None:
        broken_factory = sessionmaker(bind=create_engine("sqlite:////nonexistent/dir/db.sqlite"))
        sink = DatabaseEventSink(broken_factory)
        health = sink.health()
        self.assertEqual(health.status.value, "failed")


class TestDatabaseUrlFromConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config_dict())

    def test_builds_url_from_config_and_environ(self) -> None:
        url = database_url_from_config(
            self.cfg, environ={"POSTGRES_PASSWORD": "secret", "POSTGRES_USER": "apex", "POSTGRES_DB": "apex"}
        )
        self.assertEqual(url, "postgresql+psycopg://apex:secret@127.0.0.1:5432/apex")

    def test_missing_password_is_a_config_error_not_a_default(self) -> None:
        with self.assertRaises(ConfigError):
            database_url_from_config(self.cfg, environ={})

    def test_missing_service_is_a_config_error(self) -> None:
        cfg = parse_config(config_dict(**{"services": {}}))
        with self.assertRaises(ConfigError):
            database_url_from_config(cfg, environ={"POSTGRES_PASSWORD": "x"})


class TestEventSinkImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression pour le piège exact rencontré pendant cette phase :
    `apex.interfaces.registry.module_importable()` doit pouvoir importer
    `apex.db.event_sink` même quand l'extra `db` n'est pas installé — sinon
    `apex doctor` casserait sur "interfaces.integrity" pour une raison qui n'a
    rien à voir avec une régression du code (voir apex/db/event_sink.py)."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy" or name.startswith("sqlalchemy.")
            or name == "apex.db.event_sink" or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.db.event_sink as fresh_event_sink

                self.assertTrue(hasattr(fresh_event_sink, "DatabaseEventSink"))
        finally:
            sys.modules.pop("apex.db.event_sink", None)
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()
