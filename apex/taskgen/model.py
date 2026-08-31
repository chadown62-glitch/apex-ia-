"""Vocabulaire fermé de `TaskGenerator` — `GenerationMode`, `GeneratedTaskSpec`.

Registre : `TaskGenerator`, « self-play, bug farm, adversarial »
(`phase_defined` 5, `phase_implemented` 15) — le point central de génération
de tâches (directive Phase 15). `GenerationMode` nomme les onze intentions
listées par la directive ; seul un sous-ensemble a un backend RÉEL
aujourd'hui (`apex.taskgen.deterministic.DeterministicTaskGenerator`) — les
autres lèvent `apex.errors.NotImplementedYet`, jamais un contenu inventé
pour combler l'absence de dépendance réelle (ex. `CodeIntelligence`,
`HypothesisGenerator`, tous deux encore `PLANNED`).

`GeneratedTaskSpec` n'est PAS un `apex.tasks.model.Task` : c'est une
proposition en mémoire, jamais persistée par ce module — la persistance
reste `apex.tasks.service.TaskBankService.create()` (HUMAN seul,
`Action.CREATE_TASK`), réutilisée telle quelle par l'appelant, jamais
dupliquée ici. Aucun raccordement automatique TaskGenerator -> TaskBank
n'existe : un générateur qui écrirait lui-même sa production contournerait
le contrôle HUMAIN déjà établi en Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apex.tasks.model import TaskSplit


class GenerationMode(str, Enum):
    #: Tâche de référence, sans variation particulière.
    NORMAL = "normal"
    #: Progression paramétrée par un niveau entier (composable avec
    #: `apex.curriculum.CurriculumStrategy`).
    INCREASING_DIFFICULTY = "increasing_difficulty"
    #: Même mécanisme qu'`INCREASING_DIFFICULTY` — étiquette distincte pour
    #: une tâche explicitement demandée par `CurriculumStrategy`.
    CURRICULUM_INTERMEDIATE = "curriculum_intermediate"
    #: Niveau dérivé d'un `apex.weakness.model.WeaknessReport` — cible la
    #: sévérité détectée, PAS un sous-contrôle spécifique (la granularité
    #: nécessaire n'existe pas encore dans les données disponibles).
    TARGETED_WEAKNESS = "targeted_weakness"
    #: Reproduit exactement la commande d'une tâche existante — jamais un
    #: contenu réinventé, une régression teste l'identique.
    REGRESSION = "regression"
    #: Nécessite un catalogue de conditions limites — `PLANNED`.
    EDGE_CASE = "edge_case"
    #: Nécessite `CodeIntelligence` (`PLANNED`, phase 12) — aucun domaine de
    #: code réel n'existe encore pour fabriquer un bug légitime.
    BUG_FARM = "bug_farm"
    #: Nécessite `CodeIntelligence` (`PLANNED`, phase 12), même raison que
    #: `BUG_FARM`.
    ADVERSARIAL = "adversarial"
    #: Nécessite une seconde spécialité réellement supportée — seule
    #: `research` l'est aujourd'hui (`apex.specialties.registry`).
    TRANSFER = "transfer"
    #: Nécessite une orchestration multi-clones réelle (population, phase
    #: 17) — absente aujourd'hui.
    SELF_PLAY = "self_play"
    #: Nécessite `HypothesisGenerator` (`PLANNED`, phase 16).
    FROM_HYPOTHESIS = "from_hypothesis"


@dataclass(frozen=True)
class GeneratedTaskSpec:
    """Proposition de tâche en mémoire — jamais persistée par ce module.
    Mêmes champs que `apex.tasks.service.TaskBankService.create()` accepte,
    pour que l'appelant puisse la persister sans traduction."""

    title: str
    description: str
    command: tuple[str, ...]
    specialty: str
    split: TaskSplit
    mode: GenerationMode
    dataset_ref: str | None = None
