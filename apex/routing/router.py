"""`select_model` — sélection déterministe d'un `ModelCandidate` pour une
`RoutingRequest`, à partir de candidats déjà réels (`apex.models.registry`).

**Ce que ce module fait aujourd'hui, honnêtement** : filtre par capacité
requise, contexte minimal, disponibilité réelle (`ModelCandidate.
is_available`) et politique locale/externe (`ExternalPolicy`, jamais
contournée) ; classe les survivants par une préférence locale explicite et
un ordre déterministe (jamais aléatoire, jamais dépendant de l'ordre
d'itération d'un dict).

**Ce que ce module ne fait PAS encore, et pourquoi** : `historical_quality`/
`historical_latency`/`historical_cost`/`success_rate` (mentionnés dans la
directive comme axes futurs du score) ne sont PAS câblés ici — aucune
source de données réelle n'existe encore pour ces mesures. Les câbler
maintenant produirait un score qui a l'air de mesurer quelque chose sans
rien mesurer réellement — exactement l'erreur que `apex.obs.status.
Measurement` (REAL/UNKNOWN) existe pour empêcher ailleurs dans ce dépôt.
Le point d'entrée réel pour ces axes, le jour où assez de données
existeront, est déjà là : `EvalRun.facts["strategy"]` (ajouté lors de la
passe MetaLearner v2) porte déjà `generation_mode` par run, et
`apex.selfmodel`/`apex.costgain.CostGainEstimator` savent déjà agréger un
historique réel par spécialité — pas une nouvelle table à inventer.
"""

from __future__ import annotations

from collections.abc import Sequence

from apex.config.schema import ExternalPolicy
from apex.models.registry import ModelCandidate
from apex.routing.model import RoutingDecision, RoutingRequest, ScoredCandidate, derive_capabilities


def _passes_filters(
    candidate: ModelCandidate, request: RoutingRequest, *, external_policy: ExternalPolicy | None,
) -> tuple[bool, str]:
    if not candidate.is_available:
        return False, "aucun ModelEndpoint réel déclaré pour ce candidat"
    if not candidate.is_local:
        if not request.allow_external:
            return False, "candidat externe, allow_external=False"
        if external_policy is not None and not external_policy.enabled:
            return False, "candidat externe, ExternalPolicy.enabled=False"
    caps = derive_capabilities(candidate)
    missing = request.required_capabilities - caps
    if missing:
        return False, f"capacités manquantes : {sorted(m.value for m in missing)}"
    if request.minimum_context is not None:
        assert candidate.endpoint is not None  # garanti par is_available ci-dessus
        if candidate.endpoint.max_context < request.minimum_context:
            return False, (
                f"contexte {candidate.endpoint.max_context} < minimum requis {request.minimum_context}"
            )
    return True, "satisfait tous les critères"


def _score(candidate: ModelCandidate, request: RoutingRequest) -> tuple[float, tuple[str, ...]]:
    """Score déterministe et transparent — PAS une IA, une formule lisible.
    `1.0` de base pour tout candidat qui a passé les filtres (déjà un
    « oui » dur), `+0.5` si `prefer_local` et le candidat est local — le
    seul signal de préférence réellement disponible aujourd'hui."""
    score = 1.0
    reasons = ["satisfait les critères requis (base 1.0)"]
    if request.prefer_local and candidate.is_local:
        score += 0.5
        reasons.append("préférence locale satisfaite (+0.5)")
    return score, tuple(reasons)


def _sort_key(scored: ScoredCandidate) -> tuple[float, str, str, str]:
    """Tri déterministe : score décroissant, puis ordre alphabétique stable
    (rôle, famille, version) — jamais l'ordre d'itération d'un dict."""
    c = scored.candidate
    return (-scored.score, c.role, c.ref.family, c.ref.version)


def select_model(
    candidates: Sequence[ModelCandidate],
    request: RoutingRequest,
    *,
    external_policy: ExternalPolicy | None = None,
) -> RoutingDecision:
    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        ok, reason = _passes_filters(candidate, request, external_policy=external_policy)
        if not ok:
            scored.append(ScoredCandidate(candidate=candidate, score=0.0, reasons=(f"exclu : {reason}",)))
            continue
        value, reasons = _score(candidate, request)
        scored.append(ScoredCandidate(candidate=candidate, score=value, reasons=reasons))

    eligible = [s for s in scored if s.score > 0.0]
    ranked = tuple(sorted(scored, key=_sort_key))

    if not eligible:
        return RoutingDecision(
            selected=None, reason="aucun candidat n'a satisfait les critères de la requête",
            ranked_candidates=ranked,
        )

    best = sorted(eligible, key=_sort_key)[0]
    return RoutingDecision(
        selected=best.candidate,
        reason=f"meilleur score {best.score} parmi {len(eligible)} candidat(s) éligible(s) — {best.reasons[-1]}",
        ranked_candidates=ranked,
    )
