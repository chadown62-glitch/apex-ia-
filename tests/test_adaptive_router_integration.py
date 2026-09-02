"""Intégration bout-en-bout — passe « Adaptive Model Router V1 » :
`AgentExecution` (persistée) -> `aggregate_model_performance` ->
`select_model`.

SQLite en mémoire (même patron que `tests/test_missions_execution.py`) :
ce test vérifie l'enchaînement réel des trois couches, pas les politiques
de rôle PostgreSQL (déjà couvertes par `tests/test_db_agent_execution_
performance_columns.py`).
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine

from apex.agent_registry.performance import aggregate_model_performance
from apex.config.schema import ModelEndpoint, ModelRef
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.db.repositories import AgentExecutionRepository as DbAgentExecutionRepository
from apex.models.registry import ModelCandidate
from apex.routing.model import RoutingRequest
from apex.routing.router import select_model


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class TestAgentExecutionToRouterPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = _sqlite_session_factory()

        self.reliable_ref = ModelRef(family="qwen2.5-coder", version="7b-instruct", quantization="q4_k_m", capability_class="coder")
        self.flaky_ref = ModelRef(family="devstral-2", version="22b", quantization="q4_k_m", capability_class="coder")
        endpoint_reliable = ModelEndpoint(
            pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
            served_model="qwen2.5-coder:7b-instruct", max_context=32768,
        )
        endpoint_flaky = ModelEndpoint(
            pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
            served_model="devstral-2:22b", max_context=32768,
        )
        self.reliable_candidate = ModelCandidate(role="coder_main", ref=self.reliable_ref, resident=False, endpoint=endpoint_reliable)
        self.flaky_candidate = ModelCandidate(role="coder_main", ref=self.flaky_ref, resident=False, endpoint=endpoint_flaky)

    def _seed_execution(self, session, *, model_id: str, status: str, fallback_reason: str, elapsed_s: float | None) -> None:
        repo = DbAgentExecutionRepository(session)
        row = repo.create(id=str(uuid.uuid4()), agent_id="agent-1", mission_id="mission-1", status="running", started_at=_utcnow())
        repo.complete(
            row.id, status=status, model_id=model_id, provider_id="ollama:http://127.0.0.1:11434",
            result="ok" if status == "completed" else None,
            error=None if status == "completed" else "échec simulé pour le test",
            fallback_reason=fallback_reason, completed_at=_utcnow(), elapsed_s=elapsed_s,
        )

    def test_model_with_better_real_history_is_preferred(self) -> None:
        with self.factory() as session:
            # reliable_ref : 5 succès réels, latence moyenne 1.0s
            for _ in range(5):
                self._seed_execution(session, model_id=self.reliable_ref.id, status="completed", fallback_reason="success", elapsed_s=1.0)
            # flaky_ref : 1 succès, 4 échecs réels
            self._seed_execution(session, model_id=self.flaky_ref.id, status="completed", fallback_reason="success", elapsed_s=1.0)
            for _ in range(4):
                self._seed_execution(session, model_id=self.flaky_ref.id, status="failed", fallback_reason="provider_error", elapsed_s=None)
            session.commit()

        with self.factory() as session:
            db_repo = DbAgentExecutionRepository(session)
            from apex.agent_registry.model import AgentExecution

            reliable_executions = [AgentExecution.from_row(r) for r in db_repo.list_by_model(self.reliable_ref.id)]
            flaky_executions = [AgentExecution.from_row(r) for r in db_repo.list_by_model(self.flaky_ref.id)]

        reliable_stats = aggregate_model_performance(self.reliable_ref.id, reliable_executions)
        flaky_stats = aggregate_model_performance(self.flaky_ref.id, flaky_executions)

        self.assertEqual(reliable_stats.usage_count, 5)
        self.assertEqual(flaky_stats.usage_count, 5)
        self.assertGreater(reliable_stats.success_rate.value, flaky_stats.success_rate.value)

        performance = {self.reliable_ref.id: reliable_stats, self.flaky_ref.id: flaky_stats}
        decision = select_model(
            [self.reliable_candidate, self.flaky_candidate], RoutingRequest(), performance=performance,
        )
        self.assertEqual(decision.selected, self.reliable_candidate)

    def test_no_history_at_all_falls_back_to_pre_existing_behavior(self) -> None:
        """Aucune AgentExecution en base -> aucun ModelPerformanceStats ->
        comportement identique à avant cette passe (préférence locale
        seule)."""
        with self.factory() as session:
            db_repo = DbAgentExecutionRepository(session)
            from apex.agent_registry.model import AgentExecution

            executions = [AgentExecution.from_row(r) for r in db_repo.list_by_model(self.reliable_ref.id)]

        self.assertEqual(executions, [])
        stats = aggregate_model_performance(self.reliable_ref.id, executions)
        performance = {self.reliable_ref.id: stats}

        decision = select_model([self.reliable_candidate], RoutingRequest(), performance=performance)
        self.assertEqual(decision.ranked_candidates[0].score, 1.5)  # base 1.0 + local 0.5, inchangé


if __name__ == "__main__":
    unittest.main()
