"""Tests dédiés de `classify_outcome` (passe « Routing & Execution
Observability Hardening », N+18) — la classification RÉELLE, un mappage
total et fermé de `ModelErrorCategory` vers `FallbackReason`, jamais plus
une correspondance de sous-chaîne sur un message d'erreur. Complète
`tests/test_agent_registry_service.py::TestClassifyOutcome` (déjà présent,
inchangé) avec le chemin POSITIF : quand une catégorie réelle EST connue,
le bon `FallbackReason` en sort.
"""

from __future__ import annotations

import unittest

from apex.agent_registry.fallback import classify_outcome
from apex.agent_registry.model import FallbackReason
from apex.errors import ModelErrorCategory
from apex.routing.generation import FallbackResult, GenerationAttempt
from apex.routing.model import RoutingDecision


def _decision() -> RoutingDecision:
    return RoutingDecision(selected=object(), reason="ok", ranked_candidates=())


def _attempt(category: ModelErrorCategory | None) -> GenerationAttempt:
    return GenerationAttempt(candidate=object(), succeeded=False, error="échec simulé", result=None, category=category)


class TestRealCategoryDrivesFallbackReason(unittest.TestCase):
    def test_rate_limited_category_yields_rate_limited_reason(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.RATE_LIMITED),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.RATE_LIMITED)

    def test_auth_error_category_yields_auth_error_reason(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.AUTH_ERROR),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.AUTH_ERROR)

    def test_context_too_long_category_yields_context_too_long_reason(self) -> None:
        result = FallbackResult(
            result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.CONTEXT_TOO_LONG),),
        )
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.CONTEXT_TOO_LONG)

    def test_timeout_category_yields_timeout_reason(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.TIMEOUT),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.TIMEOUT)

    def test_offline_category_yields_offline_reason(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.OFFLINE),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.OFFLINE)

    def test_provider_error_category_yields_provider_error_reason(self) -> None:
        result = FallbackResult(
            result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.PROVIDER_ERROR),),
        )
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.PROVIDER_ERROR)

    def test_unknown_category_collapses_to_provider_error_never_a_more_specific_guess(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(ModelErrorCategory.UNKNOWN),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.PROVIDER_ERROR)

    def test_no_category_at_all_collapses_to_provider_error(self) -> None:
        result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(None),))
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.PROVIDER_ERROR)

    def test_uses_the_last_attempt_when_several_candidates_were_tried(self) -> None:
        attempts = (
            _attempt(ModelErrorCategory.RATE_LIMITED),
            _attempt(ModelErrorCategory.AUTH_ERROR),
        )
        result = FallbackResult(result=None, succeeded_with=None, attempts=attempts)
        self.assertEqual(classify_outcome(_decision(), result), FallbackReason.AUTH_ERROR)

    def test_quota_exceeded_is_never_produced_no_signal_distinguishes_it_from_rate_limited(self) -> None:
        """`FallbackReason.QUOTA_EXCEEDED` reste dans le vocabulaire fermé
        mais AUCUNE `ModelErrorCategory` n'y mappe — un HTTP 429 seul ne
        permet pas de le distinguer honnêtement de `RATE_LIMITED`."""
        for category in ModelErrorCategory:
            result = FallbackResult(result=None, succeeded_with=None, attempts=(_attempt(category),))
            self.assertNotEqual(classify_outcome(_decision(), result), FallbackReason.QUOTA_EXCEEDED)


if __name__ == "__main__":
    unittest.main()
