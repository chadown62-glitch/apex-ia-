"""Tests d'`apex.routing.router.select_model` — aucune DB, aucun réseau.

Vérifie : filtrage par capacité/contexte/disponibilité/localité, score
déterministe et transparent, tri stable, aucune sélection quand rien ne
satisfait la requête, préférence externe respectée (jamais contournée).
"""

from __future__ import annotations

import unittest

from apex.config.schema import ExternalPolicy, ModelEndpoint, ModelRef
from apex.models.registry import ModelCandidate
from apex.routing.model import Capability, RoutingRequest
from apex.routing.router import select_model


def a_ref(**overrides) -> ModelRef:
    defaults = dict(family="qwen2.5-coder", version="7b-instruct", quantization="q4_k_m", capability_class="coder")
    defaults.update(overrides)
    return ModelRef(**defaults)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local-gpu", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="qwen2.5-coder:7b-instruct", max_context=32768,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def a_candidate(**overrides) -> ModelCandidate:
    defaults = dict(role="coder_main", ref=a_ref(), resident=False, endpoint=an_endpoint())
    defaults.update(overrides)
    return ModelCandidate(**defaults)


class TestBasicSelection(unittest.TestCase):
    def test_selects_the_only_available_candidate(self) -> None:
        candidate = a_candidate()
        decision = select_model([candidate], RoutingRequest())
        self.assertEqual(decision.selected, candidate)

    def test_unavailable_candidate_without_endpoint_is_never_selected(self) -> None:
        candidate = a_candidate(endpoint=None)
        decision = select_model([candidate], RoutingRequest())
        self.assertIsNone(decision.selected)
        self.assertIn("aucun candidat n'a satisfait", decision.reason)

    def test_empty_candidate_list_selects_nothing(self) -> None:
        decision = select_model([], RoutingRequest())
        self.assertIsNone(decision.selected)
        self.assertEqual(decision.ranked_candidates, ())


class TestCapabilityFiltering(unittest.TestCase):
    def test_candidate_missing_a_required_capability_is_excluded(self) -> None:
        judge = a_candidate(role="judge", ref=a_ref(family="gemma4", capability_class="judge"))
        decision = select_model([judge], RoutingRequest(required_capabilities=frozenset({Capability.CODING})))
        self.assertIsNone(decision.selected)

    def test_candidate_with_the_required_capability_is_selected(self) -> None:
        coder = a_candidate()
        decision = select_model([coder], RoutingRequest(required_capabilities=frozenset({Capability.CODING})))
        self.assertEqual(decision.selected, coder)

    def test_multiple_required_capabilities_all_must_be_present(self) -> None:
        coder = a_candidate()  # CODING + LOCAL, pas LONG_CONTEXT (32768 < seuil si seuil > 32768)
        decision = select_model(
            [coder], RoutingRequest(required_capabilities=frozenset({Capability.CODING, Capability.VISION})),
        )
        self.assertIsNone(decision.selected)  # VISION jamais dérivée aujourd'hui


class TestContextFiltering(unittest.TestCase):
    def test_candidate_below_minimum_context_is_excluded(self) -> None:
        small = a_candidate(endpoint=an_endpoint(max_context=8192))
        decision = select_model([small], RoutingRequest(minimum_context=16384))
        self.assertIsNone(decision.selected)

    def test_candidate_meeting_minimum_context_is_selected(self) -> None:
        big = a_candidate(endpoint=an_endpoint(max_context=32768))
        decision = select_model([big], RoutingRequest(minimum_context=16384))
        self.assertEqual(decision.selected, big)


class TestExternalPolicy(unittest.TestCase):
    def _external_candidate(self) -> ModelCandidate:
        return a_candidate(
            role="cloud_role",
            ref=a_ref(family="gpt-5", version="1", quantization="external", capability_class="generalist"),
            endpoint=an_endpoint(served_model="gpt-5", runtime="external"),
        )

    def test_external_candidate_excluded_by_default(self) -> None:
        decision = select_model([self._external_candidate()], RoutingRequest())
        self.assertIsNone(decision.selected)

    def test_external_candidate_excluded_even_with_allow_external_if_policy_disabled(self) -> None:
        policy = ExternalPolicy(
            enabled=False, allowed_contexts=frozenset({"research"}), daily_budget_eur=1.0,
            monthly_budget_eur=10.0, circuit_breaker_failures=3, circuit_breaker_cooldown_s=60,
        )
        decision = select_model(
            [self._external_candidate()], RoutingRequest(allow_external=True), external_policy=policy,
        )
        self.assertIsNone(decision.selected)

    def test_external_candidate_selected_when_explicitly_allowed_and_policy_enabled(self) -> None:
        policy = ExternalPolicy(
            enabled=True, allowed_contexts=frozenset({"research"}), daily_budget_eur=1.0,
            monthly_budget_eur=10.0, circuit_breaker_failures=3, circuit_breaker_cooldown_s=60,
        )
        candidate = self._external_candidate()
        decision = select_model([candidate], RoutingRequest(allow_external=True), external_policy=policy)
        self.assertEqual(decision.selected, candidate)


class TestScoringAndOrdering(unittest.TestCase):
    def test_local_preference_ranks_local_candidate_above_external(self) -> None:
        local = a_candidate(role="a_role")
        external = a_candidate(
            role="b_role",
            ref=a_ref(family="gpt-5", version="1", quantization="external", capability_class="coder"),
            endpoint=an_endpoint(served_model="gpt-5"),
        )
        policy = ExternalPolicy(
            enabled=True, allowed_contexts=frozenset({"research"}), daily_budget_eur=1.0,
            monthly_budget_eur=10.0, circuit_breaker_failures=3, circuit_breaker_cooldown_s=60,
        )
        decision = select_model(
            [external, local], RoutingRequest(prefer_local=True, allow_external=True), external_policy=policy,
        )
        self.assertEqual(decision.selected, local)

    def test_ranking_is_deterministic_across_repeated_calls(self) -> None:
        candidates = [
            a_candidate(role="r1", ref=a_ref(family="model-b")),
            a_candidate(role="r1", ref=a_ref(family="model-a"), endpoint=an_endpoint(served_model="model-a")),
        ]
        first = select_model(candidates, RoutingRequest())
        second = select_model(candidates, RoutingRequest())
        self.assertEqual(
            [s.candidate.ref.family for s in first.ranked_candidates],
            [s.candidate.ref.family for s in second.ranked_candidates],
        )

    def test_ranked_candidates_always_include_excluded_ones_for_audit(self) -> None:
        missing_cap = a_candidate(role="judge", ref=a_ref(family="gemma4", capability_class="judge"))
        decision = select_model([missing_cap], RoutingRequest(required_capabilities=frozenset({Capability.CODING})))
        self.assertEqual(len(decision.ranked_candidates), 1)
        self.assertIn("exclu", decision.ranked_candidates[0].reasons[0])


if __name__ == "__main__":
    unittest.main()
