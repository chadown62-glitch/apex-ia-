"""`generate_with_fallback`/`generate_all_candidates` — composition de
`ModelProvider.generate()` sur une liste ordonnée de `ModelCandidate`,
jamais une nouvelle abstraction `Router`/`FallbackManager` : deux fonctions
PURES de composition (même patron que `apex.missions.execution.
launch_mission`/`apex.metalearner.cycle.run_experiment_cycle`).

**Un fallback ne masque jamais l'erreur originale** : `FallbackResult.
attempts` porte TOUJOURS la trace complète, y compris les échecs des
candidats non retenus — un appelant qui ne regarde que `result` perd cette
trace par choix, jamais parce qu'elle a été supprimée.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

from apex.errors import ModelErrorCategory, ModelProviderError, TimeoutExceeded
from apex.models.model import GenerationRequest, GenerationResult
from apex.models.provider import ModelProvider, get_default_provider
from apex.models.registry import ModelCandidate


@dataclass(frozen=True)
class GenerationAttempt:
    """`category` (passe « Routing & Execution Observability Hardening »,
    N+18) — `None` pour une tentative réussie ; sinon un signal RÉEL
    (`ModelErrorCategory`), jamais deviné depuis `error` (le texte libre)
    — `apex.agent_registry.fallback.classify_outcome` s'appuie
    exclusivement sur ce champ, jamais sur une correspondance de sous-chaîne."""

    candidate: ModelCandidate
    succeeded: bool
    error: str | None
    result: GenerationResult | None
    category: ModelErrorCategory | None = None


@dataclass(frozen=True)
class FallbackResult:
    result: GenerationResult | None
    succeeded_with: ModelCandidate | None
    attempts: tuple[GenerationAttempt, ...]


def _attempt(
    candidate: ModelCandidate, *, prompt: str, max_tokens: int | None, temperature: float | None,
    timeout_s: float, provider_factory: Callable[[ModelCandidate], ModelProvider],
    system: str | None = None, cancel_event: threading.Event | None = None,
    messages: tuple[tuple[str, str], ...] | None = None,
) -> GenerationAttempt:
    if candidate.endpoint is None:
        return GenerationAttempt(
            candidate=candidate, succeeded=False,
            error="aucun ModelEndpoint réel déclaré pour ce candidat", result=None,
            category=ModelErrorCategory.UNKNOWN,
        )
    provider = provider_factory(candidate)
    request = GenerationRequest(
        endpoint=candidate.endpoint, prompt=prompt, max_tokens=max_tokens,
        temperature=temperature, timeout_s=timeout_s, system=system, cancel_event=cancel_event,
        messages=messages,
    )
    try:
        result = provider.generate(request)
    except TimeoutExceeded as exc:
        # Exception distincte de `ModelProviderError` (jamais levée par un
        # provider comme catégorie propre) — mais la même catégorie unifiée
        # `ModelErrorCategory.TIMEOUT` sert la classification d'une tentative.
        return GenerationAttempt(
            candidate=candidate, succeeded=False, error=str(exc), result=None,
            category=ModelErrorCategory.TIMEOUT,
        )
    except ModelProviderError as exc:
        return GenerationAttempt(
            candidate=candidate, succeeded=False, error=str(exc), result=None, category=exc.category,
        )
    return GenerationAttempt(candidate=candidate, succeeded=True, error=None, result=result, category=None)


def _default_provider_factory(candidate: ModelCandidate) -> ModelProvider:
    assert candidate.endpoint is not None
    return get_default_provider(candidate.endpoint)


def _served_key(candidate: ModelCandidate) -> tuple[str, str, str] | None:
    endpoint = candidate.endpoint
    return None if endpoint is None else (endpoint.runtime, endpoint.url, endpoint.served_model)


def generate_with_fallback(
    candidates: Sequence[ModelCandidate],
    *,
    prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float = 30.0,
    provider_factory: Callable[[ModelCandidate], ModelProvider] = _default_provider_factory,
    system: str | None = None,
    cancel_event: threading.Event | None = None,
    messages: tuple[tuple[str, str], ...] | None = None,
) -> FallbackResult:
    """Essaie chaque candidat dans l'ordre fourni (déjà classé par
    `apex.routing.router.select_model`, typiquement) — s'arrête au premier
    succès. `provider_factory` est injectable pour les tests (double de
    test), `get_default_provider` par défaut (toujours `OllamaProvider`
    aujourd'hui, réel)."""
    attempts: list[GenerationAttempt] = []
    # Modèles servis déjà en échec par délai ou indisponibilité dans CET appel. Plusieurs rôles peuvent
    # désigner le MÊME modèle servi (mesuré le 06/10/2026 avec le backend natif : `coder_main` et `fast` →
    # le même modèle 1.5B) ; le relancer après un dépassement de délai doublait l'attente du Chat sans
    # aucune chance d'un autre résultat. Le saut reste une tentative tracée, jamais silencieuse.
    exhausted: dict[tuple[str, str, str], str] = {}
    for candidate in candidates:
        if cancel_event is not None and cancel_event.is_set():
            break  # annulation demandée : aucun autre candidat n'est essayé
        key = _served_key(candidate)
        if key is not None and key in exhausted:
            attempts.append(GenerationAttempt(
                candidate=candidate, succeeded=False, result=None, category=ModelErrorCategory.UNKNOWN,
                error=(f"non retenté : même modèle servi que « {exhausted[key]} », "
                       "déjà en échec dans cet appel"),
            ))
            continue
        attempt = _attempt(
            candidate, prompt=prompt, max_tokens=max_tokens, temperature=temperature,
            timeout_s=timeout_s, provider_factory=provider_factory, system=system, cancel_event=cancel_event,
            messages=messages,
        )
        attempts.append(attempt)
        if key is not None and attempt.category in (ModelErrorCategory.TIMEOUT, ModelErrorCategory.OFFLINE):
            exhausted[key] = candidate.ref.id
        if attempt.succeeded:
            return FallbackResult(result=attempt.result, succeeded_with=candidate, attempts=tuple(attempts))
    return FallbackResult(result=None, succeeded_with=None, attempts=tuple(attempts))


def generate_all_candidates(
    candidates: Sequence[ModelCandidate],
    *,
    prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float = 30.0,
    provider_factory: Callable[[ModelCandidate], ModelProvider] = _default_provider_factory,
) -> tuple[GenerationAttempt, ...]:
    """Mode multi-candidats (infrastructure seule) : interroge TOUS les
    candidats, ne s'arrête jamais au premier succès — pour une comparaison
    ultérieure. **Ne sélectionne aucune « meilleure » réponse** : comparer
    des textes libres générés par plusieurs modèles exigerait un jugement
    que `Tier0Judge` (contrat : `judge(sandbox_result, task)`, conçu pour
    un résultat de sandbox, pas un texte libre) ne couvre pas aujourd'hui —
    une vraie lacune, documentée, pas comblée par une heuristique
    improvisée ici."""
    return tuple(
        _attempt(
            candidate, prompt=prompt, max_tokens=max_tokens, temperature=temperature,
            timeout_s=timeout_s, provider_factory=provider_factory,
        )
        for candidate in candidates
    )
