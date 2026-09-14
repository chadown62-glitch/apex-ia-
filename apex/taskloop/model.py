"""Fondations de l'Autonomous Task Loop — préparation N+27 (mission N+26).

**Ce module N'IMPLÉMENTE AUCUNE BOUCLE AUTONOME.** Conformément à la
consigne explicite (« NE PAS encore transformer APEX en système totalement
autonome incontrôlé »), il pose exactement deux choses RÉELLES et
testables, sans exécuter jamais de génération/plan/exécution :

1. `TaskLoopStage` + sa machine à états (`assert_stage_transition_allowed`)
   — même discipline que `apex.missions.states`/`apex.agents.model`
   (`MissionState`/`CloneState`) : un vocabulaire fermé, des transitions
   explicitement énumérées, jamais devinées.
2. `TaskLoopStopCriteria` + `check_stop` — les garde-fous OBLIGATOIRES
   (§11 : « aucune autonomie sans garde-fous »), une fonction PURE qui
   décide s'il faut arrêter, réutilisable par un futur TaskLoop réel sans
   qu'il ait à réimplémenter cette logique.

Aucune implémentation de `OBJECTIVE -> TASK_GENERATION -> PLAN -> ...`
n'existe : un futur TaskLoop concret DOIT composer les couches déjà réelles
plutôt que les redécouvrir — `apex.recovery` (décision RETRY/REASSIGN/
BLOCK/ESCALATE, réel depuis N+26), `apex.missions.execution` (exécution
sandboxée, réel), `apex.judge`/`apex.oracles` (jugement, réel),
`apex.sandbox.CancellationToken` (annulation, réel, réutilisé ici tel
quel — jamais un second mécanisme d'annulation)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apex.sandbox.model import CancellationToken


class TaskLoopStage(str, Enum):
    """Vocabulaire FERMÉ — exactement les étapes du schéma de la mission
    N+27 (§10). Une étape non listée ici n'existe pas."""

    OBJECTIVE = "objective"
    TASK_GENERATION = "task_generation"
    PLAN = "plan"
    EXECUTION = "execution"
    TEST = "test"
    JUDGE = "judge"
    ANALYZE = "analyze"
    STRATEGY_UPDATE = "strategy_update"
    RETRY = "retry"
    REASSIGN = "reassign"
    BLOCK = "block"
    COMPLETE = "complete"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskLoopStage.COMPLETE, TaskLoopStage.BLOCK)


#: Machine à états EXACTE du schéma §10 de la mission :
#:     OBJECTIVE -> TASK_GENERATION -> PLAN -> EXECUTION -> TEST -> JUDGE
#:     JUDGE -> COMPLETE (succès) | ANALYZE (échec)
#:     ANALYZE -> STRATEGY_UPDATE -> RETRY | REASSIGN | BLOCK
#:     RETRY/REASSIGN -> PLAN (une nouvelle tentative repart du plan, jamais
#:     de l'objectif — l'objectif ne change pas entre deux tentatives)
#:
#: `EXECUTION -> ANALYZE` (N+27, `apex.taskloop.loop`) : un échec survenu
#: AVANT `TEST` (sélection de provider, génération du candidat, ou la
#: sandbox elle-même — `IsolationUnavailable`) doit pouvoir router vers
#: l'analyse d'échec SANS avoir atteint `TEST`/`JUDGE`, qui exigeraient une
#: preuve d'exécution qui n'existe pas encore dans ce cas. Ajout additif —
#: n'invalide aucune transition déjà testée par `tests/
#: test_taskloop_foundations.py` (N+26), voir sa suite pour la preuve de
#: non-régression.
_STAGE_TRANSITIONS: dict[TaskLoopStage, frozenset[TaskLoopStage]] = {
    TaskLoopStage.OBJECTIVE: frozenset({TaskLoopStage.TASK_GENERATION}),
    TaskLoopStage.TASK_GENERATION: frozenset({TaskLoopStage.PLAN}),
    TaskLoopStage.PLAN: frozenset({TaskLoopStage.EXECUTION}),
    TaskLoopStage.EXECUTION: frozenset({TaskLoopStage.TEST, TaskLoopStage.ANALYZE}),
    TaskLoopStage.TEST: frozenset({TaskLoopStage.JUDGE}),
    TaskLoopStage.JUDGE: frozenset({TaskLoopStage.COMPLETE, TaskLoopStage.ANALYZE}),
    TaskLoopStage.ANALYZE: frozenset({TaskLoopStage.STRATEGY_UPDATE}),
    TaskLoopStage.STRATEGY_UPDATE: frozenset(
        {TaskLoopStage.RETRY, TaskLoopStage.REASSIGN, TaskLoopStage.BLOCK}
    ),
    TaskLoopStage.RETRY: frozenset({TaskLoopStage.PLAN}),
    TaskLoopStage.REASSIGN: frozenset({TaskLoopStage.PLAN}),
    TaskLoopStage.BLOCK: frozenset(),
    TaskLoopStage.COMPLETE: frozenset(),
}


class InvalidTaskLoopTransition(ValueError):
    """Même contrat que `apex.missions.states.InvalidMissionTransition`/
    `apex.agents.model.InvalidTransition` : une transition absente de
    `_STAGE_TRANSITIONS` est un bug de l'appelant, jamais silencieusement
    acceptée."""


def assert_stage_transition_allowed(current: TaskLoopStage, new: TaskLoopStage) -> None:
    allowed = _STAGE_TRANSITIONS[current]
    if new not in allowed:
        raise InvalidTaskLoopTransition(
            f"transition « {current.value} » -> « {new.value} » invalide "
            f"(autorisées : {sorted(s.value for s in allowed) or 'aucune — étape terminale'})"
        )


class StopReason(str, Enum):
    """Vocabulaire FERMÉ des raisons d'arrêt — chaque valeur correspond à
    un garde-fou explicitement requis par §11 de la mission."""

    SUCCESS = "success"
    FAILURE_CRITERIA_MET = "failure_criteria_met"
    MAX_ATTEMPTS_REACHED = "max_attempts_reached"
    WALL_CLOCK_EXCEEDED = "wall_clock_exceeded"
    RESOURCE_BUDGET_EXCEEDED = "resource_budget_exceeded"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclass(frozen=True)
class TaskLoopStopCriteria:
    """Garde-fous OBLIGATOIRES d'un futur TaskLoop — §11 : « aucune
    autonomie sans garde-fous ». Aucun champ n'a de valeur par défaut
    permissive : un appelant doit les fournir explicitement, jamais hérités
    d'un défaut optimiste.

    `success_criteria`/`failure_criteria` sont des `Callable`, jamais une
    chaîne libre décrivant l'intention — la décision d'arrêt doit être
    vérifiable par du code exécutable, jamais devinée depuis du texte
    (même discipline que `apex.recovery.classify`, qui refuse toute
    classification non fondée sur une preuve structurée)."""

    max_attempts: int
    max_wall_clock_s: float
    resource_budget_description: str
    success_criteria: Callable[[Any], bool]
    failure_criteria: Callable[[Any], bool]
    #: `True` par défaut (§11 les liste comme des arrêts obligatoires) —
    #: seul un appelant qui sait CE QU'IL FAIT peut les désactiver
    #: explicitement, jamais un défaut permissif implicite.
    stop_on_infrastructure_failure: bool = True
    stop_on_provider_unavailable: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts doit être >= 1")
        if self.max_wall_clock_s <= 0:
            raise ValueError("max_wall_clock_s doit être > 0")
        if not self.resource_budget_description.strip():
            raise ValueError(
                "resource_budget_description est obligatoire — jamais de budget implicite/non documenté"
            )


def check_stop(
    *,
    attempt_number: int,
    elapsed_s: float,
    criteria: TaskLoopStopCriteria,
    last_result: Any = None,
    cancel_token: CancellationToken | None = None,
    infrastructure_failure: bool = False,
    provider_unavailable: bool = False,
) -> StopReason | None:
    """Fonction PURE — décide s'il faut arrêter, ne décide JAMAIS quoi
    faire ensuite (ça reste `apex.recovery.decide`, une couche distincte).
    `None` signifie « continuer » ; toute autre valeur est un `StopReason`
    à respecter IMMÉDIATEMENT par l'appelant.

    Ordre de vérification délibéré — l'annulation et les échecs
    d'infrastructure priment TOUJOURS sur un budget épuisé ou un critère de
    succès/échec, jamais l'inverse (un budget épuisé ne doit jamais masquer
    une annulation explicitement demandée)."""
    if cancel_token is not None and cancel_token.is_cancel_requested:
        return StopReason.CANCELLED
    if infrastructure_failure and criteria.stop_on_infrastructure_failure:
        return StopReason.INFRASTRUCTURE_FAILURE
    if provider_unavailable and criteria.stop_on_provider_unavailable:
        return StopReason.PROVIDER_UNAVAILABLE
    if criteria.success_criteria(last_result):
        return StopReason.SUCCESS
    if criteria.failure_criteria(last_result):
        return StopReason.FAILURE_CRITERIA_MET
    if attempt_number >= criteria.max_attempts:
        return StopReason.MAX_ATTEMPTS_REACHED
    if elapsed_s >= criteria.max_wall_clock_s:
        return StopReason.WALL_CLOCK_EXCEEDED
    return None
