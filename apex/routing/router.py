"""`select_model` — sélection déterministe d'un `ModelCandidate` pour une
`RoutingRequest`, à partir de candidats déjà réels (`apex.models.registry`).

**Ce que ce module fait aujourd'hui, honnêtement** : filtre par capacité
requise, contexte minimal, disponibilité réelle (`ModelCandidate.
is_available`) et politique locale/externe (`ExternalPolicy`, jamais
contournée) ; classe les survivants par un score TRANSPARENT et auditable
(`apex.routing.model.ScoreBreakdown`), jamais un chain-of-thought.

**Passe « Adaptive Model Router V1 »** : le score intègre maintenant, en
plus de la base (« satisfait les critères ») et de la préférence locale
(déjà réels depuis la première version) :
- `historical_reliability`/`historical_latency` — RÉELS SEULEMENT si un
  historique (`apex.routing.model.ModelPerformanceStats`, construit par
  `apex.agent_registry.performance.aggregate_model_performance` depuis des
  `AgentExecution` réelles) est fourni par l'appelant ET porte au moins
  une exécution exploitable pour ce candidat — sinon la contribution est
  explicitement `UNKNOWN`, jamais devinée.
- `eval_quality`/`cost` — restent explicitement `UNKNOWN` dans cette
  passe : `EvalRunner` n'est relié à aucun `ModelProvider` (aucun lien
  `model_id` ↔ verdict de qualité n'existe), et aucun prix n'est déclaré
  nulle part dans le schéma (`ModelEndpoint`/`ModelRef`). Voir le rapport
  de cette passe pour la proposition de les rendre mesurables — jamais
  simulés ici.

`performance`/`scoring_policy` sont des paramètres STRICTEMENT optionnels
(défaut `None`) : tout appel existant qui ne les fournit pas produit
EXACTEMENT la même `RoutingDecision` qu'avant cette passe — testé
explicitement (`tests/test_routing_router.py`, non-régression).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from apex.config.schema import ExternalPolicy, RouterScoringPolicy
from apex.models.registry import ModelCandidate
from apex.obs.status import DataStatus
from apex.routing.model import (
    ModelPerformanceStats,
    RoutingDecision,
    RoutingRequest,
    ScoreBreakdown,
    ScoreContribution,
    ScoredCandidate,
    derive_capabilities,
)


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


def _score_breakdown(
    candidate: ModelCandidate,
    request: RoutingRequest,
    *,
    policy: RouterScoringPolicy,
    performance: ModelPerformanceStats | None,
) -> ScoreBreakdown:
    """Décomposition NOMMÉE et auditable — jamais un nombre nu, jamais un
    chain-of-thought. Chaque `ScoreContribution` explique fonctionnellement
    sa propre valeur ; `ScoreBreakdown.total` en fait la somme (contributions
    `REAL` seulement)."""
    contributions: list[ScoreContribution] = [
        ScoreContribution(
            name="capability_match",
            value=policy.capability_match_weight,
            status=DataStatus.REAL,
            reason=f"satisfait les critères requis (poids {policy.capability_match_weight})",
        )
    ]

    local_ok = request.prefer_local and candidate.is_local
    contributions.append(
        ScoreContribution(
            name="local_preference",
            value=policy.local_preference_weight if local_ok else 0.0,
            status=DataStatus.REAL,
            reason=(
                f"préférence locale satisfaite (poids {policy.local_preference_weight})"
                if local_ok
                else "préférence locale non applicable (non demandée, ou candidat non local)"
            ),
        )
    )

    if performance is not None and performance.usage_count > 0 and performance.success_rate.status is DataStatus.REAL:
        rate = performance.success_rate.value
        prior = policy.reliability_prior_strength
        estimate = rate
        if prior:
            estimate = (performance.success_count + prior / 2) / (performance.usage_count + prior)
        smoothing = f", estimation lissée {estimate:.0%} (a priori {prior:g} obs.)" if prior else ""
        relative = policy.reliability_relative_to_prior
        if relative:
            smoothing += ", relative à l'a priori 50 %"
        contributions.append(
            ScoreContribution(
                name="historical_reliability",
                value=policy.reliability_weight * (estimate - 0.5 if relative else estimate),
                status=DataStatus.REAL,
                reason=(
                    f"taux de succès observé {rate:.0%} sur {performance.usage_count} "
                    f"exécution(s) réelle(s){smoothing} (poids {policy.reliability_weight})"
                ),
            )
        )
    else:
        contributions.append(
            ScoreContribution(
                name="historical_reliability",
                value=0.0,
                status=DataStatus.UNKNOWN,
                reason="aucun historique réel d'exécution pour ce modèle (AgentExecution)",
            )
        )

    if performance is not None and performance.mean_latency_s.status is DataStatus.REAL:
        latency = performance.mean_latency_s.value
        contribution_value = policy.latency_weight / (1.0 + latency)
        contributions.append(
            ScoreContribution(
                name="historical_latency",
                value=contribution_value,
                status=DataStatus.REAL,
                reason=(
                    f"latence moyenne observée {latency:.3f}s — décroissance harmonique "
                    f"poids/(1+latence) (poids {policy.latency_weight})"
                ),
            )
        )
    else:
        contributions.append(
            ScoreContribution(
                name="historical_latency",
                value=0.0,
                status=DataStatus.UNKNOWN,
                reason="aucune latence mesurée pour ce modèle (AgentExecution.elapsed_s)",
            )
        )

    #: Passe « Routing & Execution Observability Hardening » (N+18) —
    #: pénalité proportionnelle à un ratio d'échecs RATE_LIMITED réellement
    #: observés localement (`ModelPerformanceStats.failure_reasons`,
    #: alimenté par `apex.agent_registry.fallback.classify_outcome`,
    #: jamais un quota/coût/pourcentage d'utilisation inventé). La clé
    #: littérale `"rate_limited"` reproduit `apex.agent_registry.model.
    #: FallbackReason.RATE_LIMITED.value` SANS importer ce module (`apex.
    #: routing` ne dépend jamais d'`apex.agent_registry` — voir la
    #: docstring de `ModelPerformanceStats`).
    if performance is not None and performance.usage_count > 0:
        rate_limited_count = performance.failure_reasons.get("rate_limited", 0)
        if rate_limited_count > 0:
            risk_ratio = rate_limited_count / performance.usage_count
            contributions.append(
                ScoreContribution(
                    name="capacity_risk",
                    value=-policy.capacity_risk_weight * risk_ratio,
                    status=DataStatus.REAL,
                    reason=(
                        f"{rate_limited_count}/{performance.usage_count} exécution(s) récente(s) "
                        f"en échec par limite de débit réellement observée (poids "
                        f"{policy.capacity_risk_weight})"
                    ),
                )
            )
        else:
            contributions.append(
                ScoreContribution(
                    name="capacity_risk", value=0.0, status=DataStatus.REAL,
                    reason="aucun échec par limite de débit observé récemment pour ce modèle",
                )
            )
    else:
        contributions.append(
            ScoreContribution(
                name="capacity_risk", value=0.0, status=DataStatus.UNKNOWN,
                reason="aucun historique réel d'exécution pour ce modèle (AgentExecution) — capacité inconnue",
            )
        )

    contributions.append(
        ScoreContribution(
            name="eval_quality",
            value=0.0,
            status=DataStatus.UNKNOWN,
            reason=(
                "EvalRunner n'est relié à aucun ModelProvider — aucun verdict de qualité "
                "n'est aujourd'hui associé à un model_id (voir le rapport « Adaptive Model "
                "Router V1 »)"
            ),
        )
    )
    contributions.append(
        ScoreContribution(
            name="cost",
            value=0.0,
            status=DataStatus.UNKNOWN,
            reason="aucun prix déclaré dans le schéma (ModelEndpoint/ModelRef) — coût non mesurable",
        )
    )

    return ScoreBreakdown(contributions=tuple(contributions))


def _sort_key(scored: ScoredCandidate) -> tuple[float, str, str, str, str]:
    """Tri déterministe : score décroissant, puis rôle/famille/version/id
    alphabétique stable — jamais l'ordre d'itération d'un dict. `ref.id`
    ajouté en dernier départage (passe « Adaptive Model Router V1 ») les
    égalités exactes désormais possibles entre deux candidats partageant
    rôle/famille/version mais un `model_id` distinct (quantizations
    différentes, par ex.)."""
    c = scored.candidate
    return (-scored.score, c.role, c.ref.family, c.ref.version, c.ref.id)


def select_model(
    candidates: Sequence[ModelCandidate],
    request: RoutingRequest,
    *,
    external_policy: ExternalPolicy | None = None,
    performance: Mapping[str, ModelPerformanceStats] | None = None,
    scoring_policy: RouterScoringPolicy | None = None,
) -> RoutingDecision:
    """`performance`/`scoring_policy` optionnels — voir la docstring du
    module pour la garantie de non-régression stricte quand ils sont
    omis."""
    policy = scoring_policy if scoring_policy is not None else RouterScoringPolicy()
    perf_map = performance if performance is not None else {}

    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        ok, reason = _passes_filters(candidate, request, external_policy=external_policy)
        if not ok:
            scored.append(ScoredCandidate(candidate=candidate, score=0.0, reasons=(f"exclu : {reason}",)))
            continue
        breakdown = _score_breakdown(
            candidate, request, policy=policy, performance=perf_map.get(candidate.ref.id),
        )
        #: `reasons` (compat) ne garde que les contributions RÉELLES et
        #: STRICTEMENT POSITIVES — même sémantique que l'ancien `_score`
        #: (justifier ce qui a compté, pas auditer chaque axe vérifié).
        #: L'audit complet, axe par axe (y compris `value == 0`/`UNKNOWN`),
        #: reste dans `breakdown`, jamais perdu.
        reasons = tuple(c.reason for c in breakdown.contributions if c.status is DataStatus.REAL and c.value > 0)
        scored.append(
            ScoredCandidate(candidate=candidate, score=breakdown.total, reasons=reasons, breakdown=breakdown)
        )

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
