"""Sérialisation JSON-sûre de `RoutingDecision`/`FallbackResult` (passe
« Routing & Execution Observability Hardening », N+18) — pour que
`AgentRegistryService.run_execution` puisse écrire, dans `events` (déjà la
source de vérité réutilisée partout ailleurs dans ce dépôt, jamais un
second journal), une trace RECONSTRUCTIBLE APRÈS COUP de :

- quel candidat a été sélectionné, avec quel score et quelle décomposition
  (`ScoreBreakdown`) — contributions individuelles, statut REAL/UNKNOWN ;
- quels candidats alternatifs ont été considérés et pourquoi ils n'ont pas
  été retenus (score nul, ou moins bon score) ;
- quelle tentative a échoué, avec quelle catégorie RÉELLE, avant qu'un
  éventuel fallback ne réussisse.

**Ne conserve JAMAIS le texte généré ni le prompt** (§72) — seulement des
métadonnées (identifiants, scores, catégories, durées, comptages de
tokens) : une trace de décision, jamais un journal de contenu."""

from __future__ import annotations

from typing import Any

from apex.obs.status import DataStatus
from apex.routing.generation import FallbackResult, GenerationAttempt
from apex.routing.model import RoutingDecision, ScoredCandidate


def _serialize_scored_candidate(scored: ScoredCandidate) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "model_id": scored.candidate.ref.id,
        "score": scored.score,
        "reasons": list(scored.reasons),
    }
    if scored.breakdown is not None:
        entry["contributions"] = [
            {
                "name": c.name, "value": c.value, "status": c.status.value, "reason": c.reason,
            }
            for c in scored.breakdown.contributions
        ]
    return entry


def serialize_routing_decision(decision: RoutingDecision) -> dict[str, Any]:
    """Trace complète d'UNE décision de routage — le candidat sélectionné
    ET tous les candidats considérés (retenus ou exclus), jamais seulement
    le gagnant : c'est précisément ce qui permet de répondre après coup à
    « quels autres modèles étaient candidats, pourquoi n'ont-ils pas été
    retenus ? »."""
    return {
        "selected_model_id": decision.selected.ref.id if decision.selected is not None else None,
        "reason": decision.reason,
        "candidates": [_serialize_scored_candidate(s) for s in decision.ranked_candidates],
    }


def _serialize_attempt(attempt: GenerationAttempt) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "model_id": attempt.candidate.ref.id,
        "succeeded": attempt.succeeded,
        "error": attempt.error,
        "category": attempt.category.value if attempt.category is not None else None,
    }
    if attempt.succeeded and attempt.result is not None:
        entry["elapsed_s"] = attempt.result.elapsed_s
        entry["model_used"] = attempt.result.model_used
        entry["tokens_prompt"] = (
            attempt.result.tokens_prompt.value if attempt.result.tokens_prompt.status is DataStatus.REAL else None
        )
        entry["tokens_completion"] = (
            attempt.result.tokens_completion.value
            if attempt.result.tokens_completion.status is DataStatus.REAL else None
        )
    return entry


def serialize_fallback_result(fallback_result: FallbackResult | None) -> dict[str, Any]:
    """`None` quand aucune tentative n'a même eu lieu (aucun candidat
    éligible — voir `RoutingDecision.selected is None`)."""
    if fallback_result is None:
        return {"attempts": [], "succeeded_with_model_id": None}
    return {
        "attempts": [_serialize_attempt(a) for a in fallback_result.attempts],
        "succeeded_with_model_id": (
            fallback_result.succeeded_with.ref.id if fallback_result.succeeded_with is not None else None
        ),
    }
