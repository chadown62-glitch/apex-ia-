"""`Tier0Judge` — le seul `JudgeTier` réel de cette étape.

Délègue INTÉGRALEMENT à `OracleProvider.judge()` (`apex.oracles`, réel
depuis la Phase 5) — aucune logique de jugement dupliquée ici. C'est
exactement le fallback déjà documenté dans le registre avant cette étape :
« tier 0 objectif seul ». Aucune escalade vers TIER_1/TIER_2 : les deux
restent `PLANNED` (voir `apex.judge.provider`), donc jamais tentés, quel que
soit le verdict de tier 0 — y compris `OracleVerdict.UNKNOWN`, qui reste
`UNKNOWN` plutôt que d'être « résolu » par un palier qui n'existe pas.
"""

from __future__ import annotations

from apex.judge.model import JudgeTierLevel, JudgeVerdict
from apex.judge.provider import JudgeTier
from apex.oracles.provider import OracleProvider, get_default_oracle
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task

_NOTES = (
    "tier 0 objectif seul (ExitCodeOracle) — TIER_1 (Verifier étendu) et "
    "TIER_2 (Critic Clone, consultatif) restent PLANNED, jamais tentés."
)


class Tier0Judge(JudgeTier):
    def __init__(self, oracle: OracleProvider | None = None) -> None:
        self._oracle = oracle or get_default_oracle()

    def evaluate(self, *, sandbox_result: SandboxResult, task: Task) -> JudgeVerdict:
        result = self._oracle.judge(sandbox_result=sandbox_result, task=task)
        return JudgeVerdict(tier=JudgeTierLevel.TIER_0, oracle_result=result, notes=_NOTES)
