"""Comparaison BASELINE -> VERSION N+1 — fonctions PURES.

Règles (jamais un « tests verts = amélioré ») :

- une métrique absente/bloquée d'un côté -> BLOCKED pour cette métrique ;
- moins de `min_samples` échantillons -> INCONCLUSIVE ;
- métrique DÉTERMINISTE : comparaison exacte des moyennes, MAIS seulement
  si les deux snapshots ont été vérifiés reproductibles
  (`BenchmarkSnapshot.reproducible is True`) — sinon INCONCLUSIVE, une
  différence non reproductible n'est pas une différence ;
- métrique STOCHASTIQUE : `apex.stattest.StatTest.compare_paired`
  (réutilisé tel quel, bootstrap apparié) — NOT_SIGNIFICANT -> UNCHANGED,
  INSUFFICIENT_DATA -> INCONCLUSIVE, jamais un gain construit sur du bruit.

Agrégation (même esprit que le véto de `apex.metalearner.gauntlet`) : UNE
métrique REGRESSED suffit à rendre la version REGRESSED, quelles que
soient les autres."""

from __future__ import annotations

from apex.improvement.model import (
    BenchmarkSnapshot,
    CapabilitySpec,
    ImprovementVerdict,
    MetricComparison,
    MetricKind,
    MetricSpec,
)
from apex.stattest.comparator import StatTest
from apex.stattest.model import StatTestVerdict

_EPSILON = 1e-12


def _direction(delta: float, *, higher_is_better: bool) -> ImprovementVerdict:
    if abs(delta) <= _EPSILON:
        return ImprovementVerdict.UNCHANGED
    better = delta > 0 if higher_is_better else delta < 0
    return ImprovementVerdict.IMPROVED if better else ImprovementVerdict.REGRESSED


def compare_metric(
    metric: MetricSpec,
    before: BenchmarkSnapshot,
    after: BenchmarkSnapshot,
    *,
    stat_test: StatTest,
) -> MetricComparison:
    name = metric.name
    for label, snapshot in (("avant", before), ("après", after)):
        if name in snapshot.blocked:
            return MetricComparison(
                metric=name,
                verdict=ImprovementVerdict.BLOCKED,
                before_mean=before.mean(name),
                after_mean=after.mean(name),
                delta=None,
                reason=f"mesure {label} bloquée : {snapshot.blocked[name]}",
            )
        if name not in snapshot.samples:
            return MetricComparison(
                metric=name,
                verdict=ImprovementVerdict.BLOCKED,
                before_mean=before.mean(name),
                after_mean=after.mean(name),
                delta=None,
                reason=f"métrique absente du snapshot {label}",
            )

    a, b = before.samples[name], after.samples[name]
    before_mean, after_mean = before.mean(name), after.mean(name)
    if len(a) < metric.min_samples or len(b) < metric.min_samples:
        return MetricComparison(
            metric=name,
            verdict=ImprovementVerdict.INCONCLUSIVE,
            before_mean=before_mean,
            after_mean=after_mean,
            delta=None,
            reason=f"échantillons insuffisants ({len(a)}/{len(b)} < {metric.min_samples})",
        )
    assert before_mean is not None and after_mean is not None
    delta = after_mean - before_mean

    if metric.kind is MetricKind.DETERMINISTIC:
        if before.reproducible is not True or after.reproducible is not True:
            return MetricComparison(
                metric=name,
                verdict=ImprovementVerdict.INCONCLUSIVE,
                before_mean=before_mean,
                after_mean=after_mean,
                delta=delta,
                reason="métrique déterministe mais reproductibilité non vérifiée sur les deux snapshots",
            )
        verdict = _direction(delta, higher_is_better=metric.higher_is_better)
        return MetricComparison(
            metric=name,
            verdict=verdict,
            before_mean=before_mean,
            after_mean=after_mean,
            delta=delta,
            reason=f"comparaison exacte de mesures reproductibles (n={len(a)} -> {len(b)})",
        )

    if len(a) != len(b):
        return MetricComparison(
            metric=name,
            verdict=ImprovementVerdict.INCONCLUSIVE,
            before_mean=before_mean,
            after_mean=after_mean,
            delta=delta,
            reason=f"métrique stochastique non appariable ({len(a)} vs {len(b)} échantillons)",
        )
    result = stat_test.compare_paired(list(a), list(b))
    if result.verdict is StatTestVerdict.INSUFFICIENT_DATA:
        verdict = ImprovementVerdict.INCONCLUSIVE
    elif result.verdict is StatTestVerdict.NOT_SIGNIFICANT:
        verdict = ImprovementVerdict.UNCHANGED
    else:
        verdict = _direction(delta, higher_is_better=metric.higher_is_better)
    return MetricComparison(
        metric=name,
        verdict=verdict,
        before_mean=before_mean,
        after_mean=after_mean,
        delta=delta,
        reason=result.reason,
    )


def aggregate(comparisons: tuple[MetricComparison, ...]) -> tuple[ImprovementVerdict, str]:
    """Priorité stricte : REGRESSED > BLOCKED > INCONCLUSIVE > IMPROVED >
    UNCHANGED. Une amélioration n'est retenue que si AUCUNE métrique n'a
    régressé et qu'aucune n'est restée non concluante/bloquée."""
    if not comparisons:
        return ImprovementVerdict.INCONCLUSIVE, "aucune métrique comparée"
    by_verdict: dict[ImprovementVerdict, list[str]] = {}
    for comparison in comparisons:
        by_verdict.setdefault(comparison.verdict, []).append(comparison.metric)
    for verdict in (
        ImprovementVerdict.REGRESSED,
        ImprovementVerdict.BLOCKED,
        ImprovementVerdict.INCONCLUSIVE,
    ):
        if verdict in by_verdict:
            return verdict, f"{verdict.value} : {', '.join(sorted(by_verdict[verdict]))}"
    if ImprovementVerdict.IMPROVED in by_verdict:
        return (
            ImprovementVerdict.IMPROVED,
            f"amélioré : {', '.join(sorted(by_verdict[ImprovementVerdict.IMPROVED]))}",
        )
    return ImprovementVerdict.UNCHANGED, "aucune métrique ne change de façon mesurable"


def compare_snapshots(
    spec: CapabilitySpec,
    before: BenchmarkSnapshot,
    after: BenchmarkSnapshot,
    *,
    stat_test: StatTest | None = None,
) -> tuple[ImprovementVerdict, str, tuple[MetricComparison, ...]]:
    if before.capability_id != spec.capability_id or after.capability_id != spec.capability_id:
        raise ValueError("snapshots d'une autre capacité — comparaison refusée")
    tester = stat_test or StatTest()
    comparisons = tuple(compare_metric(m, before, after, stat_test=tester) for m in spec.metrics)
    verdict, reason = aggregate(comparisons)
    return verdict, reason, comparisons
