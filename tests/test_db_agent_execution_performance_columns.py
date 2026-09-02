"""`agent_executions.elapsed_s`/`tokens_prompt`/`tokens_completion` contre
PostgreSQL réel — migration `c3e5f7a9b1d2`, passe « Adaptive Model Router
V1 ».

SKIP explicite (jamais un PASS déguisé) si PostgreSQL n'est pas réellement
joignable — voir `tests/db_test_support.py`.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect

from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


def _engine():
    host, port = support._pg_host_port()
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{host}:{port}/{support.TEST_DB_NAME}"
    )
    return create_engine(url, future=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMigrationAddsNullableColumns(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_new_columns_exist_and_are_nullable(self) -> None:
        engine = _engine()
        columns = {c["name"]: c for c in inspect(engine).get_columns("agent_executions")}
        for name in ("elapsed_s", "tokens_prompt", "tokens_completion"):
            self.assertIn(name, columns)
            self.assertTrue(columns[name]["nullable"], f"{name} doit rester nullable")

    def test_downgrade_removes_the_three_columns(self) -> None:
        support.run_downgrade("b2d4e6f8a0c1")
        engine = _engine()
        columns = {c["name"] for c in inspect(engine).get_columns("agent_executions")}
        self.assertNotIn("elapsed_s", columns)
        self.assertNotIn("tokens_prompt", columns)
        self.assertNotIn("tokens_completion", columns)
        # ré-applique pour laisser tearDown dans un état propre
        support.run_upgrade()


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAgentExecutionRepositoryPerformanceColumns(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        self.engine = _engine()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session(self):
        from sqlalchemy.orm import Session

        return Session(self.engine)

    def test_complete_without_new_kwargs_leaves_columns_null(self) -> None:
        """Non-régression : un appelant qui ne fournit pas les nouveaux
        kwargs (tout le code existant avant cette passe) doit continuer à
        produire des colonnes NULL, jamais une valeur devinée."""
        from apex.db.repositories import AgentExecutionRepository

        with self._session() as session:
            repo = AgentExecutionRepository(session)
            row = repo.create(id=str(uuid.uuid4()), agent_id="a1", mission_id="m1", status="running", started_at=_utcnow())
            completed = repo.complete(
                row.id, status="completed", model_id="qwen2.5-coder:7b:q4_k_m", provider_id="ollama:http://x",
                result="ok", error=None, fallback_reason="success", completed_at=_utcnow(),
            )
            session.commit()
            self.assertIsNone(completed.elapsed_s)
            self.assertIsNone(completed.tokens_prompt)
            self.assertIsNone(completed.tokens_completion)

    def test_complete_with_real_measurements_persists_them(self) -> None:
        from apex.db.repositories import AgentExecutionRepository

        with self._session() as session:
            repo = AgentExecutionRepository(session)
            row = repo.create(id=str(uuid.uuid4()), agent_id="a1", mission_id="m1", status="running", started_at=_utcnow())
            completed = repo.complete(
                row.id, status="completed", model_id="qwen2.5-coder:7b:q4_k_m", provider_id="ollama:http://x",
                result="ok", error=None, fallback_reason="success", completed_at=_utcnow(),
                elapsed_s=1.234, tokens_prompt=42, tokens_completion=17,
            )
            session.commit()
            self.assertEqual(completed.elapsed_s, 1.234)
            self.assertEqual(completed.tokens_prompt, 42)
            self.assertEqual(completed.tokens_completion, 17)

    def test_list_by_model_filters_correctly(self) -> None:
        from apex.db.repositories import AgentExecutionRepository

        with self._session() as session:
            repo = AgentExecutionRepository(session)
            target_model = "qwen2.5-coder:7b:q4_k_m"
            for _ in range(2):
                row = repo.create(id=str(uuid.uuid4()), agent_id="a1", mission_id="m1", status="running", started_at=_utcnow())
                repo.complete(
                    row.id, status="completed", model_id=target_model, provider_id="ollama:http://x",
                    result="ok", error=None, fallback_reason="success", completed_at=_utcnow(), elapsed_s=1.0,
                )
            other_row = repo.create(id=str(uuid.uuid4()), agent_id="a1", mission_id="m1", status="running", started_at=_utcnow())
            repo.complete(
                other_row.id, status="completed", model_id="gemma4:12b:q4_k_m", provider_id="ollama:http://x",
                result="ok", error=None, fallback_reason="success", completed_at=_utcnow(), elapsed_s=1.0,
            )
            session.commit()

            found = repo.list_by_model(target_model)
            self.assertEqual(len(found), 2)
            self.assertTrue(all(r.model_id == target_model for r in found))


if __name__ == "__main__":
    unittest.main()
