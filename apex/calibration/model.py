"""Vocabulaire fermé de `ConfidenceCalibrator` — `CalibrationStatus`,
`CalibratedConfidence`.

Registre : `ConfidenceCalibrator`, « incertitude calibrée contre les
résultats réels » (`phase_defined` 9, `phase_implemented` 12). Trois
notions **jamais confondues** (directive Phase 12) :

- `declared_confidence` : ce qu'un modèle/clone a lui-même déclaré (donnée
  d'entrée optionnelle, simplement transmise — ce module ne l'invente ni ne
  le corrige).
- `observed_success_rate` : la performance RÉELLEMENT observée (comptage
  brut, sans lissage) — la « performance observée ».
- `calibrated_point_estimate`/`*_bound` : la « confiance calibrée »
  elle-même, un intervalle de Wilson à 95 % sur les observations, jamais un
  nombre décoratif.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CalibrationStatus(str, Enum):
    #: Échantillon sous le seuil minimal — aucune estimation calibrée
    #: n'est produite, jamais une valeur devinée pour satisfaire un champ
    #: numérique attendu.
    INSUFFICIENT_DATA = "insufficient_data"
    CALIBRATED = "calibrated"


@dataclass(frozen=True)
class CalibratedConfidence:
    status: CalibrationStatus
    sample_size: int
    #: Taux de succès brut observé (successes/total), `None` seulement si
    #: `total == 0`.
    observed_success_rate: float | None
    #: Centre de l'intervalle de Wilson à 95 % — `None` si `status`
    #: `INSUFFICIENT_DATA`.
    calibrated_point_estimate: float | None
    calibrated_lower_bound: float | None
    calibrated_upper_bound: float | None
    #: Confiance déclarée par l'appelant (ex. un modèle), si fournie — jamais
    #: calculée par ce module, seulement transmise pour comparaison.
    declared_confidence: float | None
    #: `declared_confidence - calibrated_point_estimate` — positif =
    #: surconfiance, négatif = sous-confiance. `None` si `declared_confidence`
    #: absent ou `status` `INSUFFICIENT_DATA`.
    calibration_gap: float | None
    reason: str
