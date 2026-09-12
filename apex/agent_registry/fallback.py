"""`classify_outcome` — dérive un `FallbackReason` depuis un `FallbackResult`
réel (`apex.routing.generation`), jamais une supposition sur un message
d'erreur tiers.

**N+18 (« Routing & Execution Observability Hardening »)** : la
classification s'appuie désormais exclusivement sur
`GenerationAttempt.category` (`apex.errors.ModelErrorCategory`), un signal
STRUCTURÉ produit par le provider lui-même (code HTTP réel, type
d'exception réseau) — jamais plus une correspondance de sous-chaîne sur un
message d'erreur (l'ancienne approche, fragile et documentée comme telle).
Voir la docstring de `FallbackReason` pour les valeurs qui restent
délibérément jamais produites ici (`QUOTA_EXCEEDED` : un HTTP 429 seul ne
permet pas de le distinguer honnêtement de `RATE_LIMITED`).
"""

from __future__ import annotations

from apex.agent_registry.model import FallbackReason
from apex.errors import ModelErrorCategory
from apex.routing.generation import FallbackResult
from apex.routing.model import RoutingDecision

#: Mappage FERMÉ et total (une entrée par valeur de `ModelErrorCategory`) —
#: `UNKNOWN` s'effondre sur `PROVIDER_ERROR` (nous savons qu'il y a eu un
#: échec, mais aucun signal structurel ne permet une catégorie plus
#: précise), jamais l'inverse (un échec n'est jamais silencieusement promu
#: à une catégorie qu'aucun signal ne soutient).
_CATEGORY_TO_FALLBACK_REASON: dict[ModelErrorCategory, FallbackReason] = {
    ModelErrorCategory.RATE_LIMITED: FallbackReason.RATE_LIMITED,
    ModelErrorCategory.AUTH_ERROR: FallbackReason.AUTH_ERROR,
    ModelErrorCategory.CONTEXT_TOO_LONG: FallbackReason.CONTEXT_TOO_LONG,
    ModelErrorCategory.TIMEOUT: FallbackReason.TIMEOUT,
    ModelErrorCategory.OFFLINE: FallbackReason.OFFLINE,
    ModelErrorCategory.PROVIDER_ERROR: FallbackReason.PROVIDER_ERROR,
    ModelErrorCategory.UNKNOWN: FallbackReason.PROVIDER_ERROR,
}


def classify_outcome(decision: RoutingDecision, fallback_result: FallbackResult | None) -> FallbackReason:
    """`fallback_result` est `None` quand aucun candidat n'a même été tenté
    (`decision.selected is None` avant tout appel réseau). La catégorie
    retenue est celle de la DERNIÈRE tentative — la même convention que
    l'erreur agrégée déjà construite par `apex.agent_registry.service.
    AgentRegistryService.run_execution` (`"; ".join(...)`, la dernière
    tentative reste la plus représentative de l'échec final)."""
    if fallback_result is not None and fallback_result.result is not None:
        return FallbackReason.SUCCESS
    if decision.selected is None:
        return FallbackReason.CAPABILITY_MISSING
    if fallback_result is not None and fallback_result.attempts:
        last_attempt = fallback_result.attempts[-1]
        if last_attempt.category is not None:
            return _CATEGORY_TO_FALLBACK_REASON[last_attempt.category]
    return FallbackReason.PROVIDER_ERROR
