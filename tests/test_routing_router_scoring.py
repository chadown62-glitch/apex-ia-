"""Tests d'`apex.routing.router.select_model` — axes adaptatifs, passe
« Adaptive Model Router V1 ». Séparé de `tests/test_routing_router.py`
(filtres/tri/non-régression de base) pour ne pas l'alourdir.

Vérifie : compatibilité stricte quand `performance`/`scoring_policy` sont
omis, un seul candidat, plusieurs candidats, préférence locale,
disponibilité, performance absente, gating explicite `eval_quality`/
`cost` (toujours `UNKNOWN`), déterminisme, distinction 0/aucune
donnée/unavailable dans le `ScoreBreakdown`.
"""

from __future__ import annotations

import unittest

from apex.config.schema import ModelEndpoint, ModelRef, RouterScoringPolicy
from apex.models.registry import ModelCandidate
from apex.obs.status import DataStatus, Measurement
from apex.routing.model import ModelPerformanceStats, RoutingRequest
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


def real_perf(model_id: str, *, usage_count: int, success_count: int, mean_latency_s: float | None = None) -> ModelPerformanceStats:
    return ModelPerformanceStats(
        model_id=model_id,
        usage_count=usage_count,
        success_count=success_count,
        failure_count=usage_count - success_count,
        success_rate=Measurement.real(success_count / usage_count) if usage_count else Measurement.unknown(),
        mean_latency_s=Measurement.real(mean_latency_s) if mean_latency_s is not None else Measurement.unknown(),
    )


class TestStrictBackwardCompatibility(unittest.TestCase):
    """Sans `performance`/`scoring_policy`, le score doit reproduire
    EXACTEMENT le comportement d'avant cette passe (base 1.0 + préférence
    locale 0.5)."""

    def test_single_local_candidate_scores_exactly_as_before(self) -> None:
        candidate = a_candidate()
        decision = select_model([candidate], RoutingRequest())
        self.assertEqual(decision.selected, candidate)
        self.assertEqual(decision.ranked_candidates[0].score, 1.5)

    def test_non_local_preference_scores_base_only(self) -> None:
        candidate = a_candidate()
        decision = select_model([candidate], RoutingRequest(prefer_local=False))
        self.assertEqual(decision.ranked_candidates[0].score, 1.0)

    def test_omitting_new_params_matches_explicit_none(self) -> None:
        candidate = a_candidate()
        implicit = select_model([candidate], RoutingRequest())
        explicit = select_model([candidate], RoutingRequest(), performance=None, scoring_policy=None)
        self.assertEqual(implicit.ranked_candidates[0].score, explicit.ranked_candidates[0].score)
        self.assertEqual(implicit.selected, explicit.selected)


class TestSingleCandidate(unittest.TestCase):
    def test_single_candidate_with_history_still_selected(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=5, success_count=5, mean_latency_s=1.0)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        self.assertEqual(decision.selected, candidate)


class TestMultipleCandidates(unittest.TestCase):
    def test_candidate_with_better_reliability_ranks_higher(self) -> None:
        reliable = a_candidate(ref=a_ref(version="reliable"), endpoint=an_endpoint(served_model="reliable"))
        unreliable = a_candidate(ref=a_ref(version="unreliable"), endpoint=an_endpoint(served_model="unreliable"))
        perf = {
            reliable.ref.id: real_perf(reliable.ref.id, usage_count=10, success_count=10),
            unreliable.ref.id: real_perf(unreliable.ref.id, usage_count=10, success_count=2),
        }
        decision = select_model([reliable, unreliable], RoutingRequest(), performance=perf)
        self.assertEqual(decision.selected, reliable)

    def test_deterministic_tie_break_by_model_id_when_scores_equal(self) -> None:
        a = a_candidate(ref=a_ref(version="aaa"), endpoint=an_endpoint(served_model="aaa"))
        b = a_candidate(ref=a_ref(version="bbb"), endpoint=an_endpoint(served_model="bbb"))
        decision = select_model([b, a], RoutingRequest())
        # même score (aucun historique) -> tri alphabétique stable, "aaa" avant "bbb"
        self.assertEqual(decision.selected, a)


class TestLocalPreference(unittest.TestCase):
    def test_local_preference_contribution_is_real_and_zero_when_not_applicable(self) -> None:
        candidate = a_candidate()
        decision = select_model([candidate], RoutingRequest(prefer_local=False))
        breakdown = decision.ranked_candidates[0].breakdown
        local = next(c for c in breakdown.contributions if c.name == "local_preference")
        self.assertIs(local.status, DataStatus.REAL)
        self.assertEqual(local.value, 0.0)


class TestAvailability(unittest.TestCase):
    def test_unavailable_candidate_excluded_before_scoring(self) -> None:
        candidate = a_candidate(endpoint=None)
        decision = select_model([candidate], RoutingRequest())
        self.assertIsNone(decision.selected)
        self.assertIsNone(decision.ranked_candidates[0].breakdown)


class TestPerformanceAbsent(unittest.TestCase):
    def test_missing_entry_in_performance_map_yields_unknown_contributions(self) -> None:
        candidate = a_candidate()
        other = a_candidate(ref=a_ref(version="other"), endpoint=an_endpoint(served_model="other"))
        perf = {other.ref.id: real_perf(other.ref.id, usage_count=5, success_count=5)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        breakdown = decision.ranked_candidates[0].breakdown
        reliability = next(c for c in breakdown.contributions if c.name == "historical_reliability")
        self.assertIs(reliability.status, DataStatus.UNKNOWN)
        self.assertEqual(reliability.value, 0.0)

    def test_zero_usage_count_yields_unknown_not_zero_success_rate(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=0, success_count=0)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        breakdown = decision.ranked_candidates[0].breakdown
        reliability = next(c for c in breakdown.contributions if c.name == "historical_reliability")
        self.assertIs(reliability.status, DataStatus.UNKNOWN)


class TestUnavailableAxesAlwaysVisible(unittest.TestCase):
    def test_eval_quality_and_cost_are_always_unknown_never_fabricated(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=10, success_count=10, mean_latency_s=0.5)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        breakdown = decision.ranked_candidates[0].breakdown
        names = {c.name: c for c in breakdown.contributions}
        self.assertIn("eval_quality", names)
        self.assertIn("cost", names)
        self.assertIs(names["eval_quality"].status, DataStatus.UNKNOWN)
        self.assertIs(names["cost"].status, DataStatus.UNKNOWN)
        self.assertEqual(names["eval_quality"].value, 0.0)
        self.assertEqual(names["cost"].value, 0.0)
        # Jamais silencieusement omis de la décomposition — 7 axes depuis
        # l'ajout de `capacity_risk` (N+18) : capability_match,
        # local_preference, historical_reliability, historical_latency,
        # capacity_risk, eval_quality, cost.
        self.assertEqual(len(breakdown.contributions), 7)


class TestScoringPolicyConfigurable(unittest.TestCase):
    def test_custom_scoring_policy_changes_the_total(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=10, success_count=10, mean_latency_s=1.0)}
        default_decision = select_model([candidate], RoutingRequest(), performance=perf)
        boosted_policy = RouterScoringPolicy(reliability_weight=5.0)
        boosted_decision = select_model([candidate], RoutingRequest(), performance=perf, scoring_policy=boosted_policy)
        self.assertGreater(boosted_decision.ranked_candidates[0].score, default_decision.ranked_candidates[0].score)


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_produce_identical_decision_repeatedly(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=7, success_count=6, mean_latency_s=2.5)}
        request = RoutingRequest()
        first = select_model([candidate], request, performance=perf)
        second = select_model([candidate], request, performance=perf)
        self.assertEqual(first.ranked_candidates[0].score, second.ranked_candidates[0].score)
        self.assertEqual(first.selected, second.selected)


class TestCapacityRiskAxis(unittest.TestCase):
    """Passe « Routing & Execution Observability Hardening » (N+18) — un
    axe de score fondé UNIQUEMENT sur des échecs `rate_limited` RÉELLEMENT
    observés (`ModelPerformanceStats.failure_reasons`), jamais un quota
    inventé. `UNKNOWN` sans historique, `REAL` avec une pénalité
    strictement proportionnelle sinon."""

    def _contribution(self, breakdown, name: str):
        return next(c for c in breakdown.contributions if c.name == name)

    def test_unknown_without_any_performance_data(self) -> None:
        candidate = a_candidate()
        decision = select_model([candidate], RoutingRequest())
        contribution = self._contribution(decision.ranked_candidates[0].breakdown, "capacity_risk")
        self.assertIs(contribution.status, DataStatus.UNKNOWN)
        self.assertEqual(contribution.value, 0.0)

    def test_unknown_when_usage_count_is_zero(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=0, success_count=0)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        contribution = self._contribution(decision.ranked_candidates[0].breakdown, "capacity_risk")
        self.assertIs(contribution.status, DataStatus.UNKNOWN)

    def test_real_zero_when_no_rate_limited_failures_observed(self) -> None:
        candidate = a_candidate()
        perf = {candidate.ref.id: real_perf(candidate.ref.id, usage_count=10, success_count=10)}
        decision = select_model([candidate], RoutingRequest(), performance=perf)
        contribution = self._contribution(decision.ranked_candidates[0].breakdown, "capacity_risk")
        self.assertIs(contribution.status, DataStatus.REAL)
        self.assertEqual(contribution.value, 0.0)

    def test_real_negative_penalty_proportional_to_observed_rate_limited_ratio(self) -> None:
        candidate = a_candidate()
        stats = ModelPerformanceStats(
            model_id=candidate.ref.id, usage_count=10, success_count=6, failure_count=4,
            success_rate=Measurement.real(0.6), mean_latency_s=Measurement.unknown(),
            failure_reasons={"rate_limited": 4},
        )
        policy = RouterScoringPolicy(capacity_risk_weight=1.0)
        decision = select_model([candidate], RoutingRequest(), performance={candidate.ref.id: stats}, scoring_policy=policy)
        contribution = self._contribution(decision.ranked_candidates[0].breakdown, "capacity_risk")
        self.assertIs(contribution.status, DataStatus.REAL)
        self.assertAlmostEqual(contribution.value, -0.4)  # -weight * (4/10)

    def test_capacity_risk_lowers_the_total_score_never_ignored(self) -> None:
        candidate = a_candidate()
        healthy_stats = real_perf(candidate.ref.id, usage_count=10, success_count=10)
        risky_stats = ModelPerformanceStats(
            model_id=candidate.ref.id, usage_count=10, success_count=6, failure_count=4,
            success_rate=Measurement.real(0.6), mean_latency_s=Measurement.unknown(),
            failure_reasons={"rate_limited": 4},
        )
        healthy_decision = select_model([candidate], RoutingRequest(), performance={candidate.ref.id: healthy_stats})
        risky_decision = select_model([candidate], RoutingRequest(), performance={candidate.ref.id: risky_stats})
        self.assertLess(risky_decision.ranked_candidates[0].score, healthy_decision.ranked_candidates[0].score)

    def test_other_failure_reasons_never_contribute_to_capacity_risk(self) -> None:
        """Seul `rate_limited` alimente cet axe — un échec `auth_error` ou
        `offline` observé ne doit jamais influencer un risque de CAPACITÉ,
        catégorie conceptuellement différente."""
        candidate = a_candidate()
        stats = ModelPerformanceStats(
            model_id=candidate.ref.id, usage_count=10, success_count=6, failure_count=4,
            success_rate=Measurement.real(0.6), mean_latency_s=Measurement.unknown(),
            failure_reasons={"auth_error": 4},
        )
        decision = select_model([candidate], RoutingRequest(), performance={candidate.ref.id: stats})
        contribution = self._contribution(decision.ranked_candidates[0].breakdown, "capacity_risk")
        self.assertIs(contribution.status, DataStatus.REAL)
        self.assertEqual(contribution.value, 0.0)


if __name__ == "__main__":
    unittest.main()


def _reliability(decision):
    contributions = decision.ranked_candidates[0].breakdown.contributions
    return next(c for c in contributions if c.name == "historical_reliability")


class TestReliabilitySmoothing(unittest.TestCase):
    """`reliability_prior_strength` : un succès isolé ne surclasse plus un
    dossier solide ; sans lissage (défaut), le taux brut est inchangé."""

    def setUp(self) -> None:
        self.strong = a_candidate(ref=a_ref(family="alpha", version="1"))
        self.single = a_candidate(ref=a_ref(family="beta", version="1"))
        self.performance = {
            self.strong.ref.id: real_perf(self.strong.ref.id, usage_count=10, success_count=9),
            self.single.ref.id: real_perf(self.single.ref.id, usage_count=1, success_count=1),
        }

    def choose(self, policy: RouterScoringPolicy):
        return select_model(
            [self.strong, self.single], RoutingRequest(), performance=self.performance,
            scoring_policy=policy,
        ).selected

    def test_without_smoothing_a_single_success_wins(self) -> None:
        self.assertEqual(self.choose(RouterScoringPolicy()), self.single)

    def test_with_laplace_smoothing_the_strong_record_wins(self) -> None:
        self.assertEqual(self.choose(RouterScoringPolicy(reliability_prior_strength=2.0)), self.strong)

    def test_the_smoothed_estimate_is_the_rule_of_succession(self) -> None:
        decision = select_model(
            [self.strong], RoutingRequest(), performance=self.performance,
            scoring_policy=RouterScoringPolicy(reliability_prior_strength=2.0),
        )
        reliability = _reliability(decision)
        self.assertAlmostEqual(reliability.value, 0.3 * (9 + 1) / (10 + 2))
        self.assertIn("lissée", reliability.reason)

    def test_smoothing_never_gives_a_candidate_without_history_a_contribution(self) -> None:
        decision = select_model(
            [self.single], RoutingRequest(), performance={},
            scoring_policy=RouterScoringPolicy(reliability_prior_strength=2.0),
        )
        reliability = _reliability(decision)
        self.assertEqual((reliability.value, reliability.status), (0.0, DataStatus.UNKNOWN))


class TestReliabilityRelativeToPrior(unittest.TestCase):
    """Un modèle qui échoue ne doit pas valoir un modèle jamais essayé."""

    def setUp(self) -> None:
        self.failing = a_candidate(ref=a_ref(family="alpha", version="1"))
        self.untried = a_candidate(ref=a_ref(family="beta", version="1"))
        failing_id = self.failing.ref.id
        self.performance = {failing_id: real_perf(failing_id, usage_count=2, success_count=0)}

    def choose(self, policy: RouterScoringPolicy):
        return select_model(
            [self.failing, self.untried], RoutingRequest(), performance=self.performance,
            scoring_policy=policy,
        ).selected

    def test_by_default_a_failing_model_ties_with_an_untried_one_and_config_order_wins(self) -> None:
        self.assertEqual(self.choose(RouterScoringPolicy(reliability_prior_strength=2.0)), self.failing)

    def test_relative_to_prior_a_failing_model_ranks_below_an_untried_one(self) -> None:
        policy = RouterScoringPolicy(reliability_prior_strength=2.0, reliability_relative_to_prior=True)
        self.assertEqual(self.choose(policy), self.untried)
        decision = select_model(
            [self.failing], RoutingRequest(), performance=self.performance, scoring_policy=policy
        )
        self.assertAlmostEqual(_reliability(decision).value, 0.3 * ((0 + 1) / (2 + 2) - 0.5))
