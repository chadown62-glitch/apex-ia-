"""Machine à états d'une Mission — même patron que `apex.agents.model`
(état fermé + table de transitions explicite, refus par défaut de toute
transition non listée).
"""

from __future__ import annotations

from enum import Enum


class MissionState(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELLED)


#: Transitions légales. REVIEW peut aller vers COMPLETED (validation),
#: FAILED (rejet) ou DRAFT (demande de révision) — les trois actions
#: humaines explicites exigées par la doctrine de validation humaine.
_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.DRAFT: frozenset({MissionState.QUEUED, MissionState.CANCELLED}),
    MissionState.QUEUED: frozenset({MissionState.RUNNING, MissionState.CANCELLED}),
    MissionState.RUNNING: frozenset({MissionState.REVIEW, MissionState.FAILED, MissionState.CANCELLED}),
    MissionState.REVIEW: frozenset({MissionState.COMPLETED, MissionState.FAILED, MissionState.DRAFT}),
    MissionState.COMPLETED: frozenset(),
    MissionState.FAILED: frozenset(),
    MissionState.CANCELLED: frozenset(),
}


class InvalidMissionTransition(ValueError):
    """Transition demandée absente de la machine à états — jamais rattrapée
    silencieusement, jamais un raccourci de la chaîne HUMAN -> API -> POLICY
    -> MISSION -> AGENT RUNTIME -> ... -> REVIEW -> HUMAN."""


def assert_transition_allowed(current: MissionState, target: MissionState) -> None:
    allowed = _TRANSITIONS[current]
    if target not in allowed:
        raise InvalidMissionTransition(
            f"transition « {current.value} » -> « {target.value} » invalide "
            f"(autorisées : {sorted(s.value for s in allowed) or 'aucune — état terminal'})"
        )
