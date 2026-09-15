"""Recommendation Engine — mission N+28 : transforme les `Experience`
réellement produites par le TaskLoop (N+27, `apex.taskloop.loop.
run_task_loop`) en recommandation EXPLICABLE pour une nouvelle tâche.

**N+28 recommande. N+28 ne décide pas. N+28 n'exécute pas.** Ce module
n'appelle JAMAIS `apex.taskloop`/`apex.recovery.decide`/un `ModelProvider`/
un `SandboxProvider` — aucun import de ces modules n'existe ici (vérifié
par test, pas seulement documenté, même discipline qu'`apex.metalearner.
recommendation`). Il ne fait qu'OBSERVER (lire des `Experience` déjà
persistées), ANALYSER (statistiques déterministes, aucune donnée
fabriquée) et RECOMMANDER (texte + preuve + niveau de confiance +
raison d'abstention explicite).

**Architecture : composition, pas réinvention.**

    Task (apex.tasks) / task_id
        v
    ExperienceRepository.list_by_task / TaskRepository.list_by_specialty
        v
    filtrage de pertinence (RelevanceTier)
        v
    statistiques déterministes (taux de succès, par provider, par
    stratégie, catégories d'échec dominantes — apex.metalearner.
    failure_taxonomy, réutilisé, jamais un second vocabulaire)
        v
    Recommendation (apex.metalearner.recommendation, réutilisé, jamais
    un second type) + RecommendationReport (nouveau, la trace complète)

Réutilise strictement l'existant : `apex.metalearner.experience.
{Experience,ExperienceRepository}` (N+20), `apex.metalearner.
failure_taxonomy.FailureCategory` (N+20), `apex.tasks.repository.
TaskRepository` (Phase 5, y compris `list_by_specialty`, déjà réel —
aucune nouvelle méthode de requête ajoutée à un repository), `apex.
metalearner.recommendation.{Recommendation,RecommendationConfidence,
RecommendationSource}` (préparation N+26 pour cette mission précisément —
`RecommendationConfidence` reçoit un nouveau membre `INSUFFICIENT_DATA`,
la seule extension apportée à un type existant).

**Pertinence — limitée aux données RÉELLEMENT persistées, rien inventé** :
`Experience` ne porte ni `objective`, ni `context`, ni `constraints`, ni
`execution_mode` (ce sont des champs de `apex.taskloop.request.TaskRequest`,
jamais persistés — une tâche du TaskBank, elle, expose `specialty`). La
pertinence se limite donc à deux dimensions réellement observables :

- `RelevanceTier.EXACT_TASK` — `Experience.task_id` identique à la tâche
  cible (le signal le plus fort : la même tâche, littéralement).
- `RelevanceTier.SAME_SPECIALTY` — une autre tâche du TaskBank partageant
  `Task.specialty` avec la tâche cible (signal plus large : « ce type de
  tâche », l'exemple donné par la mission — « Qwen présente actuellement
  le meilleur historique sur cette classe de tâches »).

Toute expérience dont la tâche ne partage ni l'identité ni la spécialité
n'est JAMAIS chargée ni comptée — pas un troisième niveau « non
pertinent » explicite : elle n'entre simplement jamais dans l'analyse.

**Abstention — une fonctionnalité, jamais un échec silencieux.** Si
`recommend_for_task` ne peut rien affirmer honnêtement, `recommendations`
est `()` et `abstained=True` porte une `AbstentionReason` EXPLICITE
(tâche inconnue, aucune expérience comparable, échantillon trop petit,
résultats contradictoires) — jamais une recommandation devinée."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apex.metalearner.experience import Experience
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.recommendation import Recommendation, RecommendationConfidence

#: Échantillon minimum pour dire quoi que ce soit honnêtement — mêmes
#: seuils que `apex.metalearner.recommendation.recommend_from_experience`
#: (`_confidence`), jamais une seconde échelle divergente.
_DEFAULT_MIN_SAMPLES = 3
_HIGH_CONFIDENCE_THRESHOLD = 10
_MEDIUM_CONFIDENCE_THRESHOLD = 5

#: Un groupe (provider ou stratégie) doit porter au moins CE nombre de
#: tentatives pertinentes pour entrer dans une comparaison — sinon un
#: groupe à 1 tentative fausserait la comparaison sans base statistique.
_MIN_SAMPLES_PER_GROUP = 2

#: Bande de taux de succès jugée « contradictoire » — ni clairement bon ni
#: clairement mauvais, aucune recommandation directionnelle fiable ne peut
#: en être tirée. Documenté, symétrique autour de 50 %, jamais ajusté pour
#: produire un résultat souhaité à l'avance.
_CONTRADICTORY_RATE_LOW = 0.4
_CONTRADICTORY_RATE_HIGH = 0.6

#: Écart minimum entre le meilleur et le moins bon taux de succès observé
#: pour qu'une comparaison par groupe (provider/stratégie) soit présentée
#: comme significative — sous ce seuil, l'écart pourrait n'être que du
#: bruit d'échantillon, jamais présenté comme un signal.
_MIN_SIGNIFICANT_GAP = 0.15

#: Seuil de succès au-delà duquel « aucune action » est recommandée —
#: identique à `recommend_from_experience`, jamais une seconde valeur.
_HIGH_SUCCESS_RATE_THRESHOLD = 0.7


class RelevanceTier(str, Enum):
    """Vocabulaire FERMÉ — voir la docstring du module pour pourquoi ces
    deux niveaux SEULEMENT sont réellement calculables aujourd'hui."""

    EXACT_TASK = "exact_task"
    SAME_SPECIALTY = "same_specialty"


class AbstentionReason(str, Enum):
    """Vocabulaire FERMÉ des raisons d'abstention — chacune correspond à
    un des cas explicitement listés par la mission N+28."""

    UNKNOWN_TASK = "unknown_task"
    NO_COMPARABLE_EXPERIENCE = "no_comparable_experience"
    SAMPLE_TOO_SMALL = "sample_too_small"
    CONTRADICTORY_RESULTS = "contradictory_results"


@dataclass(frozen=True)
class RecommendationReport:
    """La trace complète d'un appel à `recommend_for_task` — `Recommendation`
    (réutilisé) reste le format de CHAQUE recommandation individuelle ;
    ce type porte le contexte autour : quelle tâche, quelle spécialité,
    quelles expériences ont été jugées pertinentes (et à quel niveau),
    pourquoi une abstention a eu lieu le cas échéant. Jamais un doublon
    de `Recommendation` — une COMPOSITION explicite."""

    task_id: str
    specialty: str
    exact_task_experience_ids: tuple[str, ...]
    same_specialty_experience_ids: tuple[str, ...]
    overall_confidence: RecommendationConfidence
    recommendations: tuple[Recommendation, ...]
    abstained: bool
    abstention_reason: AbstentionReason | None
    abstention_detail: str | None

    @property
    def relevant_experience_count(self) -> int:
        return len(self.exact_task_experience_ids) + len(self.same_specialty_experience_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "specialty": self.specialty,
            "exact_task_experience_ids": list(self.exact_task_experience_ids),
            "same_specialty_experience_ids": list(self.same_specialty_experience_ids),
            "overall_confidence": self.overall_confidence.value,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason.value if self.abstention_reason else None,
            "abstention_detail": self.abstention_detail,
        }


def _confidence_for_sample(n: int) -> RecommendationConfidence:
    if n >= _HIGH_CONFIDENCE_THRESHOLD:
        return RecommendationConfidence.HIGH
    if n >= _MEDIUM_CONFIDENCE_THRESHOLD:
        return RecommendationConfidence.MEDIUM
    if n >= 1:
        return RecommendationConfidence.LOW
    return RecommendationConfidence.INSUFFICIENT_DATA


def _is_success(experience: Experience) -> bool:
    return experience.failure_category is FailureCategory.SUCCESS


def _report(
    *, task_id: str, specialty: str, exact: Sequence[Experience], partial: Sequence[Experience],
    confidence: RecommendationConfidence, recommendations: tuple[Recommendation, ...] = (),
    abstained: bool, reason: AbstentionReason | None = None, detail: str | None = None,
) -> RecommendationReport:
    return RecommendationReport(
        task_id=task_id, specialty=specialty,
        exact_task_experience_ids=tuple(e.id for e in exact),
        same_specialty_experience_ids=tuple(e.id for e in partial),
        overall_confidence=confidence, recommendations=recommendations,
        abstained=abstained, abstention_reason=reason, abstention_detail=detail,
    )


def recommend_for_task(
    task_id: str, *, session_factory: Any, min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> RecommendationReport:
    """Point d'entrée principal — jamais `decide`/`apply`/`enforce`/
    `execute`/`run_task` : cette fonction ne fait QUE lire des `Experience`
    déjà persistées et retourner un `RecommendationReport`. Aucun effet de
    bord, aucun appel réseau, aucun appel `ModelProvider`/`SandboxProvider`,
    aucune écriture en base."""
    from apex.tasks.repository import TaskRepository

    task_repo = TaskRepository(session_factory)
    task = task_repo.get(task_id)
    if task is None:
        return _report(
            task_id=task_id, specialty="inconnue", exact=(), partial=(),
            confidence=RecommendationConfidence.INSUFFICIENT_DATA, abstained=True,
            reason=AbstentionReason.UNKNOWN_TASK,
            detail=(
                f"tâche « {task_id} » introuvable dans le TaskBank — "
                "aucune expérience ne peut lui être rattachée"
            ),
        )

    from apex.metalearner.experience import ExperienceRepository

    exp_repo = ExperienceRepository(session_factory)
    exact = exp_repo.list_by_task(task_id)

    same_specialty_tasks = [t for t in task_repo.list_by_specialty(task.specialty) if t.id != task_id]
    partial: list[Experience] = []
    for other_task in same_specialty_tasks:
        partial.extend(exp_repo.list_by_task(other_task.id))

    relevant = list(exact) + partial
    total = len(relevant)

    if total == 0:
        return _report(
            task_id=task_id, specialty=task.specialty, exact=exact, partial=partial,
            confidence=RecommendationConfidence.INSUFFICIENT_DATA, abstained=True,
            reason=AbstentionReason.NO_COMPARABLE_EXPERIENCE,
            detail=(
                f"aucune expérience trouvée pour la tâche « {task_id} » ni pour "
                f"la spécialité « {task.specialty} »"
            ),
        )

    confidence = _confidence_for_sample(total)
    if total < min_samples:
        return _report(
            task_id=task_id, specialty=task.specialty, exact=exact, partial=partial,
            confidence=confidence, abstained=True, reason=AbstentionReason.SAMPLE_TOO_SMALL,
            detail=(
                f"{total} expérience(s) pertinente(s) trouvée(s) ({len(exact)} pour la tâche exacte, "
                f"{len(partial)} pour la même spécialité) — minimum requis pour recommander : {min_samples}"
            ),
        )

    recommendations: list[Recommendation] = []

    overall = _overall_success_recommendation(
        relevant, exact_count=len(exact), partial_count=len(partial), confidence=confidence,
    )
    if overall is not None:
        recommendations.append(overall)

    category_rec = _dominant_failure_category_recommendation(relevant)
    if category_rec is not None:
        recommendations.append(category_rec)

    provider_rec = _group_comparison_recommendation(
        relevant, group_fn=lambda e: e.model_id, label="provider",
    )
    if provider_rec is not None:
        recommendations.append(provider_rec)

    strategy_rec = _group_comparison_recommendation(
        relevant, group_fn=lambda e: e.strategy_id, label="stratégie",
    )
    if strategy_rec is not None:
        recommendations.append(strategy_rec)

    if not recommendations:
        return _report(
            task_id=task_id, specialty=task.specialty, exact=exact, partial=partial,
            confidence=confidence, abstained=True, reason=AbstentionReason.CONTRADICTORY_RESULTS,
            detail=(
                f"{total} expérience(s) pertinente(s) mais aucun signal exploitable "
                "(taux de succès global contradictoire, aucun groupe comparable avec un écart "
                "significatif) — aucune recommandation fiable"
            ),
        )

    return _report(
        task_id=task_id, specialty=task.specialty, exact=exact, partial=partial,
        confidence=confidence, recommendations=tuple(recommendations), abstained=False,
    )


def _overall_success_recommendation(
    experiences: Sequence[Experience], *, exact_count: int, partial_count: int,
    confidence: RecommendationConfidence,
) -> Recommendation | None:
    total = len(experiences)
    successes = sum(1 for e in experiences if _is_success(e))
    rate = successes / total
    if _CONTRADICTORY_RATE_LOW <= rate <= _CONTRADICTORY_RATE_HIGH and total >= _DEFAULT_MIN_SAMPLES:
        # Résultats trop équilibrés pour justifier une recommandation
        # directionnelle — voir la docstring du module (§ contradictoire).
        return None
    scope = (
        f"{exact_count} sur la tâche exacte, {partial_count} sur la même spécialité"
        if partial_count else f"{exact_count} sur la tâche exacte"
    )
    return Recommendation(
        observation=f"{successes}/{total} tentative(s) pertinente(s) ont réussi ({scope})",
        analysis=f"taux de succès observé : {rate:.0%} (échantillon : {total} tentative(s))",
        recommendation=(
            "aucune action recommandée — taux de succès déjà élevé sur l'échantillon observé"
            if rate >= _HIGH_SUCCESS_RATE_THRESHOLD else
            "envisager une stratégie de génération différente pour ce type de tâche "
            "(taux de succès observé sous 70 %)"
        ),
        confidence=confidence, evidence_count=total, evidence_refs=tuple(e.id for e in experiences),
    )


def _dominant_failure_category_recommendation(experiences: Sequence[Experience]) -> Recommendation | None:
    total = len(experiences)
    by_category: dict[str, list[str]] = {}
    for e in experiences:
        if not _is_success(e):
            by_category.setdefault(e.failure_category.value, []).append(e.id)
    if not by_category:
        return None
    dominant_category, dominant_ids = max(by_category.items(), key=lambda kv: len(kv[1]))
    if len(dominant_ids) < _MIN_SAMPLES_PER_GROUP:
        # Un seul échec isolé n'est pas un motif récurrent — jamais
        # présenté comme une catégorie « dominante ».
        return None
    return Recommendation(
        observation=(
            f"catégorie d'échec dominante sur les tentatives pertinentes : « {dominant_category} » "
            f"({len(dominant_ids)}/{total})"
        ),
        analysis=f"{len(dominant_ids)}/{total} échec(s) pertinent(s) classé(s) « {dominant_category} »",
        recommendation=(
            f"examiner spécifiquement les échecs « {dominant_category} » avant de relancer "
            "davantage de tentatives sur ce type de tâche"
        ),
        confidence=_confidence_for_sample(len(dominant_ids)),
        evidence_count=len(dominant_ids), evidence_refs=tuple(dominant_ids),
    )


def _group_comparison_recommendation(
    experiences: Sequence[Experience], *, group_fn: Callable[[Experience], str | None], label: str,
) -> Recommendation | None:
    """`group_fn` retourne `None` pour une expérience sans valeur connue
    (ex. `model_id` absent d'un chemin qui n'a jamais atteint la
    génération) — ces expériences sont comptées séparément
    (`unknown_count`), JAMAIS regroupées sous une fausse clé « inconnu »
    qui serait ensuite comparée comme si c'était un groupe réel."""
    groups: dict[str, list[Experience]] = {}
    unknown_count = 0
    for e in experiences:
        key = group_fn(e)
        if key is None:
            unknown_count += 1
            continue
        groups.setdefault(key, []).append(e)

    comparable = {key: exps for key, exps in groups.items() if len(exps) >= _MIN_SAMPLES_PER_GROUP}
    if len(comparable) < 2:
        return None

    stats = {
        key: (sum(1 for e in exps if _is_success(e)), len(exps))
        for key, exps in comparable.items()
    }
    rates = {key: succ / n for key, (succ, n) in stats.items()}
    best_key = max(rates, key=lambda k: rates[k])
    worst_key = min(rates, key=lambda k: rates[k])
    worst_rate = rates[worst_key]
    best_rate = rates[best_key]
    if best_rate - worst_rate < _MIN_SIGNIFICANT_GAP:
        # Écart trop faible pour être présenté comme un signal réel —
        # pourrait n'être que du bruit d'échantillon.
        return None

    ranking = ", ".join(
        f"{key}: {succ}/{n} ({succ / n:.0%})"
        for key, (succ, n) in sorted(stats.items(), key=lambda kv: -(kv[1][0] / kv[1][1]))
    )
    unknown_note = (
        f" ({unknown_count} tentative(s) sans {label} attribué, exclue(s) de cette comparaison)"
        if unknown_count else ""
    )
    total_compared = sum(n for _succ, n in stats.values())
    evidence = tuple(e.id for exps in comparable.values() for e in exps)
    return Recommendation(
        observation=f"comparaison par {label} sur les tentatives pertinentes : {ranking}{unknown_note}",
        analysis=(
            f"écart observé entre le meilleur historique (« {best_key} », {best_rate:.0%}) "
            f"et le moins bon (« {worst_key} », {worst_rate:.0%}) : {best_rate - worst_rate:.0%}"
        ),
        recommendation=(
            f"« {best_key} » présente actuellement le meilleur historique observé par {label} "
            "pour ce type de tâche"
        ),
        confidence=_confidence_for_sample(total_compared),
        evidence_count=total_compared, evidence_refs=evidence,
    )


class ExperienceRecommendationEngine:
    """Implémente `apex.metalearner.recommendation.RecommendationSource` —
    même contrat, jamais un second Protocol. `recommend()` n'expose QUE le
    tuple de `Recommendation` (comme le contrat l'exige) ; le
    `RecommendationReport` complet (pertinence/abstention) reste
    accessible via `recommend_for_task`, la fonction que cette classe
    délègue entièrement — aucune logique dupliquée ici."""

    def __init__(self, *, session_factory: Any, min_samples: int = _DEFAULT_MIN_SAMPLES) -> None:
        self._session_factory = session_factory
        self._min_samples = min_samples

    def recommend(self, *, task_id: str) -> tuple[Recommendation, ...]:
        return recommend_for_task(
            task_id, session_factory=self._session_factory, min_samples=self._min_samples,
        ).recommendations
