"""`ConfidenceCalibrator` — calibration par intervalle de Wilson, phase 12.

Aucune dépendance DB : prend des compteurs `successes`/`total` déjà
rassemblés par l'appelant (ex. `apex.weakness.model.WeaknessReport.pass_count`/
`meaningful_sample_size`) — même séparation logique/DB que
`apex.weakness.detector.WeaknessDetector`. `calibrate_from_weakness_report`
est un raccourci qui réutilise EXACTEMENT le même échantillon que
`WeaknessDetector` (PASS+FAIL, ERROR/TIMEOUT/UNKNOWN exclus) — zéro
définition divergente de ce qui compte comme « résultat exploitable ».

Intervalle de Wilson (méthode statistique standard, pas inventée ici) plutôt
qu'un simple `successes/total` : à petit échantillon, un taux brut est une
fausse précision — l'intervalle rend l'incertitude explicite et se resserre
naturellement quand le volume de preuve augmente. stdlib pur (`math`),
cohérent avec ADR-0002 (noyau sans dépendance).
"""

from __future__ import annotations

import math

from apex.calibration.model import CalibratedConfidence, CalibrationStatus
from apex.weakness.model import WeaknessReport

#: Sous ce nombre d'observations PASS/FAIL, aucune calibration n'est
#: tentée — un intervalle de Wilson sur 1-2 points est trop large pour être
#: qualifié de « calibré » plutôt que de « pas assez de données ».
MIN_SAMPLE_FOR_CALIBRATION = 3

#: z pour un intervalle bilatéral à 95 % (loi normale standard).
_Z_95 = 1.959963984540054


class ConfidenceCalibrator:
    def calibrate(
        self, *, successes: int, total: int, declared_confidence: float | None = None,
    ) -> CalibratedConfidence:
        if total < 0 or successes < 0:
            raise ValueError("successes et total doivent être >= 0")
        if successes > total:
            raise ValueError("successes ne peut pas dépasser total")
        if declared_confidence is not None and not (0.0 <= declared_confidence <= 1.0):
            raise ValueError("declared_confidence doit être dans [0.0, 1.0]")

        observed = (successes / total) if total > 0 else None

        if total < MIN_SAMPLE_FOR_CALIBRATION:
            return CalibratedConfidence(
                status=CalibrationStatus.INSUFFICIENT_DATA, sample_size=total,
                observed_success_rate=observed, calibrated_point_estimate=None,
                calibrated_lower_bound=None, calibrated_upper_bound=None,
                declared_confidence=declared_confidence, calibration_gap=None,
                reason=(
                    f"n={total} < seuil minimal {MIN_SAMPLE_FOR_CALIBRATION} — intervalle de "
                    "Wilson non significatif en dessous de ce seuil, aucune valeur calibrée produite"
                ),
            )

        center, lower, upper = self._wilson_interval(successes, total)
        gap = (declared_confidence - center) if declared_confidence is not None else None
        return CalibratedConfidence(
            status=CalibrationStatus.CALIBRATED, sample_size=total, observed_success_rate=observed,
            calibrated_point_estimate=center, calibrated_lower_bound=lower, calibrated_upper_bound=upper,
            declared_confidence=declared_confidence, calibration_gap=gap,
            reason=f"intervalle de Wilson à 95 % sur n={total} observations réelles",
        )

    def calibrate_from_weakness_report(
        self, report: WeaknessReport, *, declared_confidence: float | None = None,
    ) -> CalibratedConfidence:
        return self.calibrate(
            successes=report.pass_count, total=report.meaningful_sample_size,
            declared_confidence=declared_confidence,
        )

    @staticmethod
    def _wilson_interval(successes: int, total: int) -> tuple[float, float, float]:
        z = _Z_95
        n = total
        p = successes / n
        denom = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denom
        margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
        return center, max(0.0, center - margin), min(1.0, center + margin)
