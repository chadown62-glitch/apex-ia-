"""Vocabulaire fermé de `WeaknessDetector` — `WeaknessCategory`, `WeaknessReport`.

Registre : `WeaknessDetector`, « détection d'écarts de compétence »
(`phase_defined` 5, `phase_implemented` 12). Distingue au minimum
(directive Phase 12) : manque de données / absence de faiblesse / échec
ponctuel / faiblesse répétée / faiblesse statistiquement crédible — jamais
plus de précision que les données ne le permettent.

**Ce module ne fait AUCUN calcul** : voir `apex.weakness.detector` pour la
logique. Il ne fait que déclarer le vocabulaire et la forme d'un rapport
traçable — même séparation vocabulaire/logique qu'`apex.oracles.model` vis-à-vis
d'`apex.oracles.exit_code`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WeaknessCategory(str, Enum):
    #: Aucun résultat PASS/FAIL exploitable — jamais une faiblesse inventée
    #: parce qu'une donnée manque.
    INSUFFICIENT_DATA = "insufficient_data"
    #: Assez de résultats exploitables, aucun échec observé.
    NO_WEAKNESS = "no_weakness"
    #: Un seul échec, échantillon trop petit pour conclure à un motif.
    ISOLATED_FAILURE = "isolated_failure"
    #: Plusieurs échecs observés, mais échantillon encore sous le seuil
    #: requis pour une conclusion statistiquement crédible.
    REPEATED_WEAKNESS = "repeated_weakness"
    #: Taux d'échec au-dessus du seuil heuristique, sur un échantillon
    #: suffisant. **Heuristique documentée, PAS un `StatTest` réel**
    #: (`StatTest` reste `PLANNED`, phase 16) : ce module ne prétend jamais
    #: à une preuve statistique rigoureuse, seulement à un signal explicite
    #: et reproductible à partir d'un seuil fixe.
    STATISTICALLY_CREDIBLE_WEAKNESS = "statistically_credible_weakness"


@dataclass(frozen=True)
class WeaknessReport:
    """Un rapport toujours traçable : `evidence_run_ids` permet de retrouver
    exactement quels `EvalRun` ont produit cette conclusion — jamais une
    conclusion sans preuve citable.

    Les compteurs ERROR/TIMEOUT/UNKNOWN sont conservés séparément et
    **exclus** du calcul PASS/FAIL : un échec d'infrastructure (OOM, réseau
    refusé, annulation) n'est jamais confondu avec une réponse incorrecte de
    la tâche elle-même — même distinction que `apex.oracles`.
    """

    category: WeaknessCategory
    pass_count: int
    fail_count: int
    error_count: int
    timeout_count: int
    unknown_count: int
    #: `None` uniquement si `pass_count + fail_count == 0` — jamais un taux
    #: fabriqué faute de mieux.
    failure_rate: float | None
    evidence_run_ids: tuple[str, ...]
    reason: str

    @property
    def meaningful_sample_size(self) -> int:
        return self.pass_count + self.fail_count
