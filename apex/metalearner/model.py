"""Modèle de décision de `MetaLearner` — `StrategyDecision`.

Registre : `MetaLearner`, « apprendre comment apprendre » (`phase_defined`
5, `phase_implemented` 22). `mode` réutilise DIRECTEMENT
`apex.taskgen.model.GenerationMode` (Phase 15) — aucun second vocabulaire
de stratégie inventé : la « Strategy Selection » de la directive EST le
choix d'un `GenerationMode`, rien de plus, rien d'à côté.
"""

from __future__ import annotations

from dataclasses import dataclass

from apex.curriculum.model import CurriculumDecision
from apex.evalrunner.model import EvalRun
from apex.taskgen.model import GenerationMode
from apex.tasks.model import Task
from apex.weakness.model import WeaknessReport


@dataclass(frozen=True)
class StrategyDecision:
    """`mode` est `None` UNIQUEMENT quand `weakness_report` ne porte aucun
    signal exploitable (`INSUFFICIENT_DATA`) — jamais une stratégie
    devinée faute de preuve. `curriculum_decision` reste la source de
    `level` : `MetaLearner` ne réimplémente pas la logique de progression,
    il la COMPOSE (`apex.curriculum.CurriculumStrategy`, Phase 12)."""

    specialty: str
    mode: GenerationMode | None
    level: int
    curriculum_decision: CurriculumDecision
    reason: str


@dataclass(frozen=True)
class ExperimentCycleResult:
    """La trace complète d'UN cycle réel — `apex.metalearner.cycle.
    run_experiment_cycle` — traçable de bout en bout : quelle décision a
    motivé quelle tâche, exécutée par quel clone, avec quel résultat, et
    comment la faiblesse a évolué. Jamais une conclusion sans preuve
    citable (`eval_run.id`, `task.id`)."""

    decision: StrategyDecision
    task: Task
    eval_run: EvalRun
    weakness_before: WeaknessReport
    weakness_after: WeaknessReport
