"""Machine à états du pipeline de conception — même patron que
`apex.missions.states`/`apex.operator.states` : Enum fermée + table de
transitions fermée (`_TRANSITIONS`) + exception dédiée + fonction de garde
`assert_transition_allowed` qui refuse par défaut toute transition absente
de la table.

Ordre des étapes métier (REQUEST -> ... -> PACKAGE), chacune correspondant
à un état `DesignPipelineState` distinct pendant son exécution :
ANALYZE, RESEARCH, SYNTHESIZE, DESIGN, VISUALIZE, VALIDATE, PACKAGE.
"""

from __future__ import annotations

from enum import Enum


class DesignStep(str, Enum):
    """Les 7 étapes métier du pipeline, dans leur ordre d'exécution fixe."""

    ANALYZE = "analyze"
    RESEARCH = "research"
    SYNTHESIZE = "synthesize"
    DESIGN = "design"
    VISUALIZE = "visualize"
    VALIDATE = "validate"
    PACKAGE = "package"


#: Ordre total et fixe — jamais réordonné dynamiquement.
DESIGN_STEP_ORDER: tuple[DesignStep, ...] = (
    DesignStep.ANALYZE,
    DesignStep.RESEARCH,
    DesignStep.SYNTHESIZE,
    DesignStep.DESIGN,
    DesignStep.VISUALIZE,
    DesignStep.VALIDATE,
    DesignStep.PACKAGE,
)


class DesignStepState(str, Enum):
    """État d'exécution d'UNE étape (`apex.design.model.StepRecord.state`).

    `SKIPPED` est distinct de `FAILED` : une étape qui constate honnêtement
    l'absence d'un backend réel (recherche web, génération visuelle) et le
    documente sans lever n'est pas un échec du pipeline — c'est le cas
    normal en l'absence de ces backends, jamais fabriqué comme un succès.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class DesignPipelineState(str, Enum):
    """État global du pipeline — dérivé de l'étape en cours, jamais une
    donnée indépendante à maintenir en double."""

    REQUEST = "request"
    ANALYZING = "analyzing"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    DESIGNING = "designing"
    VISUALIZING = "visualizing"
    VALIDATING = "validating"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (DesignPipelineState.COMPLETED, DesignPipelineState.FAILED)


#: Une étape en échec (FAILED) peut reprendre à N'IMPORTE quelle étape de
#: traitement — `resume()` (apex.design.pipeline) choisit la cible exacte
#: selon la dernière étape réellement en échec, jamais cette table seule.
_TRANSITIONS: dict[DesignPipelineState, frozenset[DesignPipelineState]] = {
    DesignPipelineState.REQUEST: frozenset({DesignPipelineState.ANALYZING, DesignPipelineState.FAILED}),
    DesignPipelineState.ANALYZING: frozenset(
        {DesignPipelineState.RESEARCHING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.RESEARCHING: frozenset(
        {DesignPipelineState.SYNTHESIZING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.SYNTHESIZING: frozenset(
        {DesignPipelineState.DESIGNING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.DESIGNING: frozenset(
        {DesignPipelineState.VISUALIZING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.VISUALIZING: frozenset(
        {DesignPipelineState.VALIDATING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.VALIDATING: frozenset(
        {DesignPipelineState.PACKAGING, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.PACKAGING: frozenset(
        {DesignPipelineState.COMPLETED, DesignPipelineState.FAILED}
    ),
    DesignPipelineState.COMPLETED: frozenset(),
    DesignPipelineState.FAILED: frozenset(
        {
            DesignPipelineState.ANALYZING,
            DesignPipelineState.RESEARCHING,
            DesignPipelineState.SYNTHESIZING,
            DesignPipelineState.DESIGNING,
            DesignPipelineState.VISUALIZING,
            DesignPipelineState.VALIDATING,
            DesignPipelineState.PACKAGING,
        }
    ),
}


class InvalidDesignTransition(ValueError):
    """Transition absente de `_TRANSITIONS` — refusée, jamais tolérée."""

    def __init__(self, current: DesignPipelineState, target: DesignPipelineState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"transition de pipeline de conception invalide : {current.value} -> {target.value}")


def assert_transition_allowed(current: DesignPipelineState, target: DesignPipelineState) -> None:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise InvalidDesignTransition(current, target)


def step_to_state(step: DesignStep) -> DesignPipelineState:
    """Mappage fixe étape -> état « en cours » correspondant."""
    return {
        DesignStep.ANALYZE: DesignPipelineState.ANALYZING,
        DesignStep.RESEARCH: DesignPipelineState.RESEARCHING,
        DesignStep.SYNTHESIZE: DesignPipelineState.SYNTHESIZING,
        DesignStep.DESIGN: DesignPipelineState.DESIGNING,
        DesignStep.VISUALIZE: DesignPipelineState.VISUALIZING,
        DesignStep.VALIDATE: DesignPipelineState.VALIDATING,
        DesignStep.PACKAGE: DesignPipelineState.PACKAGING,
    }[step]
