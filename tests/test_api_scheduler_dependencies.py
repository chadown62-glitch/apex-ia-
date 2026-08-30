"""Tests du singleton Scheduler applicatif — `apex.api.dependencies`
(étape 11, arbitrage humain point A) : jamais recréé par requête, mais
réinitialisable EXPLICITEMENT pour les tests qui recréent la base
PostgreSQL sous le même nom entre deux cas — voir la docstring du module
pour pourquoi un déploiement réel n'a jamais besoin de ce mécanisme.
"""

from __future__ import annotations

import os
import threading
import unittest

from apex.api.dependencies import (
    DatabaseUnavailable,
    get_scheduler_service,
    reset_scheduler_service_for_tests,
)
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


class TestSingletonWithoutDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        reset_scheduler_service_for_tests()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        reset_scheduler_service_for_tests()

    def test_construction_failure_is_never_cached(self) -> None:
        """Sans DB configurée, chaque appel échoue — jamais un singleton
        construit-mais-cassé mis en cache après le premier échec (voir la
        docstring de get_scheduler_service : l'assignation au global n'a
        lieu qu'après un succès intégral)."""
        with self.assertRaises(DatabaseUnavailable):
            get_scheduler_service()
        with self.assertRaises(DatabaseUnavailable):
            get_scheduler_service()


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestSingletonWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        reset_scheduler_service_for_tests()

    def tearDown(self) -> None:
        reset_scheduler_service_for_tests()
        support.drop_test_database()
        support.drop_application_roles()

    def test_repeated_calls_return_the_identical_instance(self) -> None:
        first = get_scheduler_service()
        second = get_scheduler_service()
        self.assertIs(first, second)

    def test_concurrent_first_calls_construct_exactly_one_instance(self) -> None:
        """Double-checked locking : plusieurs threads appelant
        get_scheduler_service() pour la toute première fois ne doivent
        jamais produire deux instances distinctes."""
        instances: list = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def call() -> None:
            barrier.wait()
            svc = get_scheduler_service()
            with lock:
                instances.append(svc)

        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(instances), 8)
        self.assertEqual(len({id(i) for i in instances}), 1)

    def test_reset_shuts_down_the_executor_and_forces_reconstruction(self) -> None:
        first = get_scheduler_service()
        reset_scheduler_service_for_tests()
        second = get_scheduler_service()
        self.assertIsNot(first, second)
        # L'ancien executor est bien arrêté — soumettre dessus après shutdown lève,
        # preuve qu'aucun thread résiduel de l'ancien singleton ne peut survivre.
        with self.assertRaises(RuntimeError):
            first._executor.submit(lambda: None)

    def test_first_call_runs_recovery_reconciliation(self) -> None:
        """RecoveryService.reconcile_on_startup() est appelé à la première
        construction du singleton dans ce processus — même sur une base
        fraîche/vide, une trace d'audit est toujours écrite (consigne étape
        8 : l'absence d'orphelin est aussi un fait à tracer, jamais silencieux)."""
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config, session_scope
        from apex.db.repositories import AuditLogRepository

        get_scheduler_service()
        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        with session_scope(factory) as session:
            logs = AuditLogRepository(session).recent(limit=200)
        matching = [row for row in logs if row.action == "scheduler.recovery_reconciled"]
        self.assertTrue(matching)


if __name__ == "__main__":
    unittest.main()
