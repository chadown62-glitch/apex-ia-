"""`StatTest` — bootstrap apparié, stdlib pur (ADR-0002 : noyau sans
dépendance — pas de `numpy`/`scipy`).

Compare deux échantillons APPARIÉS (même longueur, paire par paire — ex.
méthode A vs méthode B sur les MÊMES tâches) et estime un intervalle de
confiance bootstrap sur leur différence moyenne. Zéro état conservé entre
deux appels : principe 10 (« validité continue, accumulation multi-nuits »)
est satisfait par construction — ré-appeler avec un échantillon plus grand
ne fait que produire une nouvelle décision, indépendante de la précédente,
jamais une mise à jour d'un état figé.

Déterministe pour une graine (`seed`) donnée — reproductible, jamais un
résultat qui varie d'une exécution à l'autre pour les mêmes données.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from apex.stattest.model import StatTestResult, StatTestVerdict

#: Sous ce nombre de paires, aucune décision n'est tentée — un intervalle
#: bootstrap sur moins de 5 paires est trop instable pour être qualifié de
#: décision statistique plutôt que de bruit.
MIN_PAIRS = 5


class StatTest:
    def __init__(self, *, n_bootstrap: int = 2000, confidence_level: float = 0.95, seed: int = 0) -> None:
        if n_bootstrap < 100:
            raise ValueError("n_bootstrap doit être >= 100 pour un intervalle exploitable")
        if not (0.0 < confidence_level < 1.0):
            raise ValueError("confidence_level doit être dans ]0.0, 1.0[")
        self._n_bootstrap = n_bootstrap
        self._confidence_level = confidence_level
        self._seed = seed

    def compare_paired(self, a: Sequence[float], b: Sequence[float]) -> StatTestResult:
        if len(a) != len(b):
            raise ValueError(f"échantillons non appariés : len(a)={len(a)}, len(b)={len(b)}")
        n = len(a)

        if n < MIN_PAIRS:
            return StatTestResult(
                verdict=StatTestVerdict.INSUFFICIENT_DATA, observed_diff=None, ci_lower=None,
                ci_upper=None, confidence_level=self._confidence_level, sample_size=n,
                reason=f"n={n} < seuil minimal {MIN_PAIRS} — bootstrap non significatif en dessous",
            )

        observed = (sum(b) - sum(a)) / n
        rng = random.Random(self._seed)
        diffs: list[float] = []
        for _ in range(self._n_bootstrap):
            indices = [rng.randrange(n) for _ in range(n)]
            resampled_a = sum(a[i] for i in indices) / n
            resampled_b = sum(b[i] for i in indices) / n
            diffs.append(resampled_b - resampled_a)
        diffs.sort()

        alpha = 1.0 - self._confidence_level
        lower_idx = max(0, int((alpha / 2) * self._n_bootstrap))
        upper_idx = min(self._n_bootstrap - 1, int((1 - alpha / 2) * self._n_bootstrap))
        ci_lower, ci_upper = diffs[lower_idx], diffs[upper_idx]

        significant = not (ci_lower <= 0.0 <= ci_upper)
        verdict = StatTestVerdict.SIGNIFICANT if significant else StatTestVerdict.NOT_SIGNIFICANT
        reason = (
            f"IC bootstrap {self._confidence_level:.0%} sur n={n} paires ({self._n_bootstrap} "
            f"tirages) = [{ci_lower:.4f}, {ci_upper:.4f}] — "
            + ("exclut 0, différence distinguable du bruit" if significant else "contient 0, non distinguable du bruit")
        )
        return StatTestResult(
            verdict=verdict, observed_diff=observed, ci_lower=ci_lower, ci_upper=ci_upper,
            confidence_level=self._confidence_level, sample_size=n, reason=reason,
        )
