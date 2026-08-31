"""Contrat `JudgeTier` — même patron que `apex.oracles.provider`/
`apex.sandbox.provider` : un contrat abstrait minimal, un seul point de
choix d'implémentation (`get_default_judge_tier`), rien d'autre.

Périmètre strict de cette étape (Phase 9, `JudgeTier` PLANNED -> STUB) :
seul `Tier0Judge` existe réellement — un palier objectif qui délègue
entièrement à `OracleProvider` (`apex.oracles`, réel depuis la Phase 5),
zéro duplication de la logique de jugement. TIER_1 (Verifier étendu) et
TIER_2 (Critic Clone, consultatif) restent `PLANNED`, non implémentés : le
contrat accepte déjà `task` en paramètre (comme `OracleProvider.judge`) pour
ne pas avoir à changer de signature le jour où un palier supérieur existera.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.judge.model import JudgeVerdict
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task


class JudgeTier(ABC):
    @abstractmethod
    def evaluate(self, *, sandbox_result: SandboxResult, task: Task) -> JudgeVerdict:
        """Produit un jugement hiérarchisé. Ne lève jamais — même discipline
        qu'`OracleProvider.judge` : un jugement impossible reste `UNKNOWN`
        (porté par `JudgeVerdict.oracle_result.verdict`), jamais une
        exception ni un verdict deviné."""


def get_default_judge_tier() -> JudgeTier:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un palier plus riche (Tier1Judge,
    Tier2Judge) serait choisi une fois réellement implémenté. Aujourd'hui :
    `Tier0Judge`, le seul palier réel."""
    from apex.judge.tier0 import Tier0Judge

    return Tier0Judge()
