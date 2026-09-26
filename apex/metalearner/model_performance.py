"""Signal de performance du routeur tiré des `Experience` jugées.

    Experience (verdict de l'oracle, preuve de succès) -> ModelPerformanceStats
    -> apex.routing.router.select_model(performance=...)

Règles, par expérience rattachée à un candidat (`model_id == served_model` de
son endpoint — exactement, jamais par préfixe) :

- succès CONFIRMÉ par ré-exécution -> succès du modèle ;
- succès seulement OBSERVÉ -> ignoré (aucune preuve dans un sens ou l'autre) ;
- succès RÉFUTÉ -> échec du modèle (le même code ne se reproduit pas) ;
- échec dû au code généré (réponse fausse, candidat invalide, délai,
  ressources, politique, exception) -> échec du modèle ;
- panne d'infrastructure ou d'oracle -> ignorée : jamais imputée au modèle.

Les exécutions rejouées pour confirmation (`model_id = "replay:..."`) ne
correspondent à aucun endpoint : jamais comptées comme des tentatives
indépendantes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from apex.metalearner.experience import Experience, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.models.registry import ModelCandidate
from apex.obs.status import Measurement
from apex.routing.model import ModelPerformanceStats

NOT_ATTRIBUTABLE = frozenset({FailureCategory.INFRASTRUCTURE_FAILURE, FailureCategory.TEST_ORACLE_FAILURE})


def _outcome(experience: Experience) -> bool | None:
    """True = succès du modèle, False = échec du modèle, None = ne compte pas."""
    if experience.failure_category is FailureCategory.SUCCESS:
        if experience.success_evidence is SuccessEvidence.CONFIRMED:
            return True
        if experience.success_evidence is SuccessEvidence.REFUTED:
            return False
        return None
    if experience.failure_category in NOT_ATTRIBUTABLE:
        return None
    return False


def performance_from_experiences(
    experiences: Iterable[Experience], candidates: Sequence[ModelCandidate]
) -> dict[str, ModelPerformanceStats]:
    by_served = {c.endpoint.served_model: c.ref.id for c in candidates if c.endpoint is not None}
    outcomes: dict[str, list[tuple[bool, Experience]]] = {ref_id: [] for ref_id in by_served.values()}
    for experience in experiences:
        ref_id = by_served.get(experience.model_id or "")
        outcome = _outcome(experience)
        if ref_id is not None and outcome is not None:
            outcomes[ref_id].append((outcome, experience))

    stats: dict[str, ModelPerformanceStats] = {}
    for ref_id, rows in outcomes.items():
        if not rows:
            continue
        successes = sum(1 for ok, _ in rows if ok)
        failures = [e for ok, e in rows if not ok]
        reasons: dict[str, int] = {}
        for experience in failures:
            reasons[experience.failure_category.value] = (
                reasons.get(experience.failure_category.value, 0) + 1
            )
        latest_failure = max(failures, key=lambda e: e.created_at, default=None)
        stats[ref_id] = ModelPerformanceStats(
            model_id=ref_id,
            usage_count=len(rows),
            success_count=successes,
            failure_count=len(failures),
            success_rate=Measurement.real(successes / len(rows)),
            mean_latency_s=Measurement.unknown(),
            failure_reasons=reasons,
            last_failure_reason=latest_failure.failure_category.value if latest_failure else None,
        )
    return stats
