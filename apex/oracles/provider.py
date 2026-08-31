"""Contrat `OracleProvider` — même patron que `apex.sandbox.provider`/
`apex.models.provider` (étapes 7/6) : un contrat abstrait minimal, un seul
point de choix d'implémentation (`get_default_oracle`), rien d'autre.

Périmètre strict de cette étape (Phase 5) : seul `ExitCodeOracle` existe
réellement — jugement par code de sortie du processus sandboxé. Un oracle
plus riche (comparaison de sortie à une valeur attendue, jugement par
`ModelProvider`, ...) reste `PLANNED`, non anticipé ici ; le contrat prend
déjà `task` en paramètre (pas seulement `sandbox_result`) précisément pour
ne pas avoir à changer la signature le jour où un tel oracle existera.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.oracles.model import OracleResult
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task


class OracleProvider(ABC):
    @abstractmethod
    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        """Juge la correction d'une tentative. Ne lève jamais — un jugement
        impossible à porter est `OracleVerdict.UNKNOWN`, pas une exception."""


def get_default_oracle() -> OracleProvider:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un oracle différent serait choisi une
    fois réellement implémenté. Aujourd'hui : `ExitCodeOracle`, le seul
    oracle réel."""
    from apex.oracles.exit_code import ExitCodeOracle

    return ExitCodeOracle()
