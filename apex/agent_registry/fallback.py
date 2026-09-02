"""`classify_outcome` — dérive un `FallbackReason` depuis un `FallbackResult`
réel (`apex.routing.generation`), jamais une supposition sur un message
d'erreur tiers. Voir la docstring de `FallbackReason` pour la liste des
valeurs jamais produites ici, faute de signal réel.
"""

from __future__ import annotations

from apex.agent_registry.model import FallbackReason
from apex.routing.generation import FallbackResult
from apex.routing.model import RoutingDecision

#: Sous-chaîne STABLE — c'est notre propre `apex.errors.TimeoutExceeded.
#: __str__`, jamais un message tiers deviné (voir `FallbackReason`).
_TIMEOUT_MARKER = "timeout dépassé sur"
#: Sous-chaîne STABLE — c'est notre propre `apex.routing.router._passes_
#: filters`, jamais un message tiers deviné.
_CAPABILITY_MARKER = "capacités manquantes"


def classify_outcome(decision: RoutingDecision, fallback_result: FallbackResult | None) -> FallbackReason:
    """`fallback_result` est `None` quand aucun candidat n'a même été tenté
    (`decision.selected is None` avant tout appel réseau)."""
    if fallback_result is not None and fallback_result.result is not None:
        return FallbackReason.SUCCESS
    if decision.selected is None:
        if _CAPABILITY_MARKER in decision.reason:
            return FallbackReason.CAPABILITY_MISSING
        return FallbackReason.CAPABILITY_MISSING
    if fallback_result is not None:
        for attempt in fallback_result.attempts:
            if attempt.error and _TIMEOUT_MARKER in attempt.error:
                return FallbackReason.TIMEOUT
    return FallbackReason.PROVIDER_ERROR
