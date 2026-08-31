"""Vocabulaire fermé de `StatTest` — `StatTestVerdict`, `StatTestResult`.

Registre : `StatTest`, « décision statistique, preuve cumulable »
(`phase_defined` 5, `phase_implemented` 16). Principe 10 : « validité
continue, accumulation multi-nuits » — chaque appel est indépendant, sans
état conservé entre deux exécutions ; ré-exécuter avec plus de paires ne
fait que produire une nouvelle décision valide, jamais une décision figée.

Ce vocabulaire **remplace** les heuristiques fixes documentées ailleurs
(`apex.weakness.detector.CREDIBLE_FAILURE_RATE_THRESHOLD`, explicitement
disqualifiée comme « PAS un StatTest réel ») pour tout appelant qui a besoin
d'une VRAIE décision statistique — ce module ne modifie PAS
`WeaknessDetector` lui-même (contrat déjà validé, Phase 12), il fournit
seulement l'outil que ce dernier ne prétendait pas être.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StatTestVerdict(str, Enum):
    #: Échantillon sous le seuil minimal — aucune décision statistique
    #: n'est tentée, jamais un verdict fabriqué faute de données.
    INSUFFICIENT_DATA = "insufficient_data"
    #: L'intervalle de confiance bootstrap de la différence contient zéro —
    #: aucune différence distinguable du bruit à ce niveau de confiance.
    NOT_SIGNIFICANT = "not_significant"
    #: L'intervalle de confiance bootstrap exclut zéro.
    SIGNIFICANT = "significant"


@dataclass(frozen=True)
class StatTestResult:
    verdict: StatTestVerdict
    #: mean(b) - mean(a) — positif si b > a.
    observed_diff: float | None
    ci_lower: float | None
    ci_upper: float | None
    confidence_level: float
    sample_size: int
    reason: str
