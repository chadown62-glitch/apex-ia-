"""`TaskRequest` — la demande d'exécution d'une `apex.tasks.model.Task`
existante (TaskBank, portant un `solution_harness`) via `apex.taskloop.loop.
run_task_loop` — N+27.

**Pourquoi une `Task` du TaskBank, jamais une `Mission`** : `apex.missions`
exige un palier `REVIEW` (validation HUMAINE explicite) avant `COMPLETED` —
incompatible avec un « SUCCESS -> COMPLETED » jugé automatiquement par un
Oracle, que cette mission exige. `apex.tasks.model.Task`/`apex.oracles`
portent déjà, depuis la Phase 5/N+19, exactement le vocabulaire de jugement
evidence-based (`OracleVerdict.PASS/FAIL/ERROR/TIMEOUT/UNKNOWN`) qu'un
TaskLoop autonome borné doit consommer — voir `apex.taskloop.loop` pour le
raisonnement complet.

`TaskExecutionMode` est un vocabulaire FERMÉ à trois valeurs — délibérément
SANS AUCUNE valeur « autonomous »/« autonomous_forever » : impossible de
construire un `TaskRequest` qui demande une autonomie non bornée, par
construction du type, pas seulement par convention documentée."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from apex.policy.model import Subject


class TaskExecutionMode(str, Enum):
    """Niveaux d'autonomie LEVEL 0/1/2 de la mission N+27 — LEVEL 3 (« fully
    autonomous ») N'EST PAS un membre de ce vocabulaire, aujourd'hui ni plus
    tard sans une revue explicite de ce module.

    `MANUAL`/`ASSISTED` sont honnêtement IDENTIQUES au niveau mécanique de
    `run_task_loop` (aucune confirmation humaine intermédiaire réelle
    n'existe encore) : les deux forcent `TaskLimits.max_attempts` à `1`
    (aucune boucle de récupération, une seule tentative) — jamais présentés
    comme fonctionnellement distincts tant que cette différence n'est pas
    réellement implémentée. Seul `BOUNDED` active la boucle bornée
    RETRY/REASSIGN/BLOCK/ESCALATE de `apex.recovery`."""

    MANUAL = "manual"
    ASSISTED = "assisted"
    BOUNDED = "bounded"


@dataclass(frozen=True)
class TaskLimits:
    """Bornes OBLIGATOIRES — §11 de la mission : « aucune condition ne doit
    permettre une boucle infinie ». Aucune valeur par défaut permissive :
    chaque champ a un défaut FAIBLE (une seule tentative, un budget court),
    jamais un défaut optimiste qui masquerait un oubli de l'appelant.

    Quatre bornes DISTINCTES, jamais fusionnées : `max_attempts` (compteur
    global, toutes catégories/actions confondues — alimente `apex.config.
    schema.RecoveryPolicy.max_attempts`, comme `launch_mission` le fait déjà
    pour les missions), `max_retries` (compte UNIQUEMENT les décisions
    `RecoveryAction.RETRY`, un sous-ensemble strict de `max_attempts` —
    permet de borner spécifiquement « retenter avec le même provider », même
    si le budget global de tentatives est plus large), `max_candidates`
    (combien de `ModelCandidate` DISTINCTS `RecoveryAction.REASSIGN` peut
    réellement essayer), `max_wall_clock_s` (durée réelle, via
    `apex.taskloop.model.check_stop`)."""

    max_attempts: int = 1
    max_retries: int = 1
    max_candidates: int = 1
    max_wall_clock_s: float = 300.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts doit être >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries doit être >= 0")
        if self.max_candidates < 1:
            raise ValueError("max_candidates doit être >= 1")
        if self.max_wall_clock_s <= 0:
            raise ValueError("max_wall_clock_s doit être > 0")


@dataclass(frozen=True)
class TaskRequest:
    """`task_id` référence une `apex.tasks.model.Task` DÉJÀ persistée
    (TaskBank) portant un `solution_harness` — voir la docstring du module.
    `context`/`constraints` sont des textes ADDITIFS, jamais une réécriture
    de `Task.description` (même discipline que `apex.evalrunner.service.
    EvalRunnerService.run_generated_solution`'s `prompt_suffix`) : le modèle
    voit toujours `task.description`, complétée, jamais remplacée.

    `authorization` est le `Subject` sous lequel CHAQUE appel à
    `AgentRuntime.spawn`/`execute_in_sandbox` est effectué — aucune nouvelle
    action `PolicyEngine` n'est introduite : ce champ ne fait que choisir QUI
    demande, l'autorisation elle-même reste entièrement celle déjà appliquée
    par `AgentRuntime`."""

    task_id: str
    objective: str
    context: str = ""
    constraints: str = ""
    execution_mode: TaskExecutionMode = TaskExecutionMode.BOUNDED
    limits: TaskLimits = field(default_factory=TaskLimits)
    authorization: Subject = Subject.CORE
    #: Id d'une TaskRun TERMINÉE de la même tâche dont les `Experience` conditionnent la stratégie de
    #: génération (règle existante `apex.metalearner.adaptive_solution_loop.
    #: decide_strategy_from_experiences`). `None` (défaut) : aucune expérience lue, comportement inchangé —
    #: c'est le bras « baseline » de l'expérience RUN 1 -> RUN 2.
    condition_on_run: str | None = None

    def effective_max_attempts(self) -> int:
        """`MANUAL`/`ASSISTED` forcent une seule tentative — voir la
        docstring de `TaskExecutionMode`."""
        if self.execution_mode is TaskExecutionMode.BOUNDED:
            return self.limits.max_attempts
        return 1
