"""Tests d'`apex.agent_registry.performance.aggregate_model_performance` —
aucune DB, aucun réseau (fonction pure, même discipline que
`apex.costgain.estimator.CostGainEstimator`).

Vérifie : cas vide explicitement supporté, agrégation homogène (tout
succès), agrégation mixte (échecs variés), distinction stricte entre
« 0 » (fait réel, ex. aucune exécution) et « aucune donnée »
(`Measurement.unknown()`, ex. latence jamais mesurée), déterminisme.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from apex.agent_registry.model import AgentExecution, AgentExecutionStatus, FallbackReason
from apex.agent_registry.performance import aggregate_model_performance
from apex.obs.status import DataStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def an_execution(**overrides) -> AgentExecution:
    defaults = dict(
        id="exec-1", agent_id="agent-1", mission_id="mission-1",
        status=AgentExecutionStatus.COMPLETED, model_id="qwen2.5-coder:7b:q4_k_m",
        provider_id="ollama:http://127.0.0.1:11434", result="ok", error=None,
        fallback_reason=FallbackReason.SUCCESS, started_at=_utcnow(), completed_at=_utcnow(),
        elapsed_s=None, tokens_prompt=None, tokens_completion=None,
    )
    defaults.update(overrides)
    return AgentExecution(**defaults)


class TestEmptyAggregation(unittest.TestCase):
    def test_empty_sequence_yields_zero_usage_and_unknown_rate(self) -> None:
        stats = aggregate_model_performance("some-model", [])
        self.assertEqual(stats.model_id, "some-model")
        self.assertEqual(stats.usage_count, 0)
        self.assertEqual(stats.success_count, 0)
        self.assertEqual(stats.failure_count, 0)
        # 0 exécution est un FAIT réel (usage_count), jamais confondu avec
        # une donnée manquante (success_rate reste UNKNOWN, jamais 0.0).
        self.assertIs(stats.success_rate.status, DataStatus.UNKNOWN)
        self.assertIs(stats.mean_latency_s.status, DataStatus.UNKNOWN)
        self.assertEqual(stats.failure_reasons, {})


class TestHomogeneousAggregation(unittest.TestCase):
    def test_all_successes_with_latency_gives_real_stats(self) -> None:
        executions = [
            an_execution(id="e1", elapsed_s=1.0),
            an_execution(id="e2", elapsed_s=3.0),
        ]
        stats = aggregate_model_performance("m1", executions)
        self.assertEqual(stats.usage_count, 2)
        self.assertEqual(stats.success_count, 2)
        self.assertEqual(stats.failure_count, 0)
        self.assertIs(stats.success_rate.status, DataStatus.REAL)
        self.assertEqual(stats.success_rate.value, 1.0)
        self.assertIs(stats.mean_latency_s.status, DataStatus.REAL)
        self.assertEqual(stats.mean_latency_s.value, 2.0)

    def test_usage_count_real_even_when_no_execution_has_latency(self) -> None:
        """0 utilisation est réel ; usage_count > 0 mais latence jamais
        mesurée est un cas DISTINCT — mean_latency_s reste UNKNOWN, pas 0."""
        executions = [an_execution(id="e1", elapsed_s=None)]
        stats = aggregate_model_performance("m1", executions)
        self.assertEqual(stats.usage_count, 1)
        self.assertIs(stats.mean_latency_s.status, DataStatus.UNKNOWN)
        self.assertIs(stats.success_rate.status, DataStatus.REAL)  # usage_count > 0


class TestMixedAggregation(unittest.TestCase):
    def test_mixed_success_and_failures_counts_correctly(self) -> None:
        executions = [
            an_execution(id="e1", status=AgentExecutionStatus.COMPLETED, elapsed_s=1.0),
            an_execution(id="e2", status=AgentExecutionStatus.FAILED, fallback_reason=FallbackReason.TIMEOUT, result=None),
            an_execution(id="e3", status=AgentExecutionStatus.FAILED, fallback_reason=FallbackReason.PROVIDER_ERROR, result=None),
            an_execution(id="e4", status=AgentExecutionStatus.FAILED, fallback_reason=FallbackReason.TIMEOUT, result=None),
        ]
        stats = aggregate_model_performance("m1", executions)
        self.assertEqual(stats.usage_count, 4)
        self.assertEqual(stats.success_count, 1)
        self.assertEqual(stats.failure_count, 3)
        self.assertAlmostEqual(stats.success_rate.value, 0.25)
        self.assertEqual(stats.failure_reasons, {"timeout": 2, "provider_error": 1})

    def test_partial_token_measurements_average_only_known_values(self) -> None:
        executions = [
            an_execution(id="e1", tokens_prompt=100, tokens_completion=50),
            an_execution(id="e2", tokens_prompt=None, tokens_completion=None),
        ]
        stats = aggregate_model_performance("m1", executions)
        self.assertIs(stats.mean_tokens_prompt.status, DataStatus.REAL)
        self.assertEqual(stats.mean_tokens_prompt.value, 100.0)
        self.assertIs(stats.mean_tokens_completion.status, DataStatus.REAL)
        self.assertEqual(stats.mean_tokens_completion.value, 50.0)


class TestDeterminism(unittest.TestCase):
    def test_repeated_calls_on_same_input_produce_identical_output(self) -> None:
        executions = [
            an_execution(id="e1", elapsed_s=1.5, tokens_prompt=10, tokens_completion=20),
            an_execution(id="e2", status=AgentExecutionStatus.FAILED, fallback_reason=FallbackReason.TIMEOUT, result=None),
        ]
        first = aggregate_model_performance("m1", executions)
        second = aggregate_model_performance("m1", executions)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
