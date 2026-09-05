"""Contrat `ResponseJudge` — passe « Real Model Evaluation ».

Périmètre exact du besoin : `OracleProvider.judge(sandbox_result, task)`
(`apex.oracles.provider`) juge un PROCESSUS SANDBOXÉ (code de sortie réel,
preuve noyau) — il ne peut structurellement pas juger un texte libre généré
par un `ModelProvider`, puisqu'aucun processus n'existe pour cette
tentative. Aucun mécanisme existant du dépôt ne couvre ce cas (vérifié :
`JudgeTier`/`Tier0Judge` délèguent entièrement à `OracleProvider`, donc
héritent de la même limite).

`ResponseJudge` est donc un contrat FRÈRE, jamais un remplacement :
il réutilise intégralement le vocabulaire déjà réel `OracleResult`/
`OracleVerdict` (`apex.oracles.model`) — aucun second vocabulaire de
correction n'est inventé, exactement la même discipline que `JudgeVerdict`
enveloppant déjà `OracleResult` sans le dupliquer.

V1 délibérément minimale — **jamais un « LLM-as-a-judge »** : la seule
implémentation réelle, `ExactMatchResponseJudge`, compare la réponse
générée à `Task.expected_answer` par égalité de chaîne (déterministe,
reproductible, zéro dépendance à un second modèle, trivialement testable).
Quand `expected_answer` n'est pas déclaré sur la tâche, le verdict est
`OracleVerdict.UNKNOWN` — jamais deviné — exactement la même discipline que
`ExitCodeOracle` sur un statut non reconnu.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.models.model import GenerationResult
from apex.oracles.model import OracleResult, OracleVerdict
from apex.tasks.model import Task


class ResponseJudge(ABC):
    @abstractmethod
    def judge(self, *, response: GenerationResult, task: Task) -> OracleResult:
        """Juge la correction d'une réponse générée par un `ModelProvider`.
        Ne lève jamais — un jugement impossible à porter est
        `OracleVerdict.UNKNOWN`, jamais deviné (même discipline que
        `OracleProvider.judge`)."""


def get_default_response_judge() -> ResponseJudge:
    """Seul point de construction utilisé par de futurs consommateurs —
    c'est ICI, et seulement ici, qu'un jugement plus riche serait choisi une
    fois réellement implémenté. Aujourd'hui : `ExactMatchResponseJudge`, le
    seul `ResponseJudge` réel."""
    return ExactMatchResponseJudge()


class ExactMatchResponseJudge(ResponseJudge):
    """Compare `response.text` à `task.expected_answer` — égalité de chaîne
    après normalisation minimale (espaces de tête/fin retirés des deux
    côtés, jamais une normalisation plus agressive qui risquerait de faire
    correspondre deux réponses réellement différentes)."""

    def judge(self, *, response: GenerationResult, task: Task) -> OracleResult:
        if task.expected_answer is None:
            return OracleResult(
                verdict=OracleVerdict.UNKNOWN,
                detail="aucune « expected_answer » déclarée sur la tâche — jugement non évaluable",
            )
        if response.text.strip() == task.expected_answer.strip():
            return OracleResult(
                verdict=OracleVerdict.PASS,
                detail="réponse générée strictement identique à « expected_answer » (comparaison exacte)",
            )
        return OracleResult(
            verdict=OracleVerdict.FAIL,
            detail="réponse générée différente de « expected_answer » (comparaison exacte)",
        )
