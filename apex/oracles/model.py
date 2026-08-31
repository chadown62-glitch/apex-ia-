"""Vocabulaire fermé de jugement — `OracleVerdict`, `OracleResult`.

**Jamais confondu avec `apex.sandbox.model.ExecutionStatus`** (SUCCESS/
FAILED/TIMEOUT/OOM_KILLED/...) : `ExecutionStatus` répond à « le processus
sandboxé a-t-il terminé, et comment ? » (une question d'exécution). `OracleVerdict`
répond à « la tâche a-t-elle été résolue ? » (une question de correction),
un vocabulaire délibérément distinct, exactement les cinq catégories exigées
(GO explicite Phase 5) : succès / échec / erreur d'exécution / timeout /
résultat inconnu-non évaluable. Un run peut par exemple avoir
`ExecutionStatus.SUCCESS` (le processus s'est terminé proprement) mais un
`OracleVerdict` autre que `PASS` si un futur `OracleProvider` plus riche
qu'`ExitCodeOracle` juge la sortie elle-même incorrecte — ce module ne
préjuge d'aucune stratégie de jugement particulière.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OracleVerdict(str, Enum):
    #: La tâche a été résolue.
    PASS = "pass"
    #: La tâche a été tentée et n'a pas été résolue (le processus a
    #: réellement terminé, avec une sortie jugée incorrecte).
    FAIL = "fail"
    #: L'exécution elle-même a échoué pour une raison indépendante de la
    #: correction de la tâche (limite ressource dépassée, réseau refusé,
    #: isolation indisponible, ...) — jamais confondu avec FAIL.
    ERROR = "error"
    #: Le délai imparti a été dépassé sans résultat.
    TIMEOUT = "timeout"
    #: Résultat non évaluable (ex. exécution annulée avant complétion) —
    #: jamais un FAIL ou un PASS deviné faute de mieux.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OracleResult:
    verdict: OracleVerdict
    detail: str
