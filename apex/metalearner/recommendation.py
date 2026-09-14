"""Frontière Learning -> Orchestrateur — préparation N+28 (mission N+26).

**Principe verrouillé, répété dans TOUT ce module** : LEARNING != DECISION
AUTHORITY. Une source de recommandation peut OBSERVER (lire des
`Experience` déjà persistées, réel depuis N+20), ANALYSER (calculer des
statistiques RÉELLES sur ces observations) et RECOMMANDER (produire un
`Recommendation` en texte + preuve). Elle ne DÉCIDE jamais rien : aucune
fonction de ce module n'appelle `apex.recovery.decide`, ne modifie
`apex.config.schema.RecoveryPolicy`, ni aucune autre politique
opérationnelle. `RecommendationSource` (le contrat) ne définit
DÉLIBÉRÉMENT aucune méthode `decide()` — c'est la garantie structurelle,
pas seulement documentée.

`recommend_from_experience` N'EST PAS un stub : elle interroge réellement
`apex.metalearner.experience.ExperienceRepository` (réel depuis N+20) et
calcule des statistiques réelles. Aucun appelant de ce dépôt ne consomme
aujourd'hui sa sortie pour modifier automatiquement une politique — c'est
exactement la frontière que cette mission doit établir : la fonction
EXISTE et fonctionne, mais rien ne lui donne d'autorité de décision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from apex.metalearner.failure_taxonomy import FailureCategory


class RecommendationConfidence(str, Enum):
    """Reflète UNIQUEMENT la taille de l'échantillon observé — jamais une
    estimation de qualité de la recommandation elle-même (ce serait une
    seconde opinion non fondée empilée sur la première)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Recommendation:
    """`observation` (fait brut) -> `analysis` (statistique calculée) ->
    `recommendation` (texte, jamais une action exécutable) — les trois
    champs restent SÉPARÉS explicitement : jamais un seul texte qui
    mélangerait fait et interprétation, pour qu'un lecteur (humain ou futur
    TaskLoop) puisse distinguer ce qui est mesuré de ce qui est suggéré."""

    observation: str
    analysis: str
    recommendation: str
    confidence: RecommendationConfidence
    evidence_count: int
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation,
            "analysis": self.analysis,
            "recommendation": self.recommendation,
            "confidence": self.confidence.value,
            "evidence_count": self.evidence_count,
            "evidence_refs": list(self.evidence_refs),
        }


class RecommendationSource(Protocol):
    """Contrat qu'une future source de recommandation (un autre module
    `apex.metalearner`, ou un futur composant N+28) doit respecter.
    Volontairement dépourvu de toute méthode `decide`/`apply`/`enforce` —
    ce n'est pas un oubli, c'est la frontière elle-même : rien qui
    implémente ce Protocol ne peut, par construction, prétendre décider."""

    def recommend(self, *, task_id: str) -> tuple[Recommendation, ...]: ...


def recommend_from_experience(
    task_id: str, *, session_factory: Any, min_samples: int = 3,
) -> tuple[Recommendation, ...]:
    """Implémentation RÉELLE — agrège les `Experience` déjà persistées pour
    cette tâche. `()` (aucune recommandation) si l'échantillon est trop
    petit pour dire quoi que ce soit honnêtement — jamais une recommandation
    fabriquée sur une preuve insuffisante."""
    from apex.metalearner.experience import ExperienceRepository

    experiences = ExperienceRepository(session_factory).list_by_task(task_id)
    if len(experiences) < min_samples:
        return ()

    total = len(experiences)
    successes = sum(1 for e in experiences if e.failure_category is FailureCategory.SUCCESS)
    by_category: dict[str, list[str]] = {}
    for e in experiences:
        if e.failure_category is not FailureCategory.SUCCESS:
            by_category.setdefault(e.failure_category.value, []).append(e.id)

    def _confidence(n: int) -> RecommendationConfidence:
        if n >= 10:
            return RecommendationConfidence.HIGH
        if n >= 5:
            return RecommendationConfidence.MEDIUM
        return RecommendationConfidence.LOW

    success_rate = successes / total
    recommendations = [
        Recommendation(
            observation=f"{successes}/{total} tentatives ont réussi pour la tâche « {task_id} »",
            analysis=f"taux de succès observé : {success_rate:.0%} (échantillon : {total} tentatives)",
            recommendation=(
                "aucune action recommandée — taux de succès déjà élevé sur l'échantillon observé"
                if success_rate >= 0.7 else
                "envisager une stratégie de génération différente pour cette tâche "
                "(taux de succès observé sous 70 %)"
            ),
            confidence=_confidence(total),
            evidence_count=total,
            evidence_refs=tuple(e.id for e in experiences),
        )
    ]

    if by_category:
        dominant_category, dominant_ids = max(by_category.items(), key=lambda kv: len(kv[1]))
        recommendations.append(
            Recommendation(
                observation=(
                    f"catégorie d'échec dominante pour « {task_id} » : "
                    f"{dominant_category} ({len(dominant_ids)}/{total} tentatives)"
                ),
                analysis=f"{len(dominant_ids)}/{total} échecs classés « {dominant_category} »",
                recommendation=(
                    f"examiner spécifiquement les échecs « {dominant_category} » avant "
                    "de relancer davantage de tentatives sur cette tâche"
                ),
                confidence=_confidence(len(dominant_ids)),
                evidence_count=len(dominant_ids),
                evidence_refs=tuple(dominant_ids),
            )
        )

    return tuple(recommendations)
