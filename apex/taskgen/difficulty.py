"""`Difficulty` — modèle de difficulté structuré (passe « Universal Task +
Curriculum Engine », N+11, § Phase 7 de la mission).

Réutilise EXACTEMENT l'échelle entière 1-4 déjà réelle
(`apex.metalearner.challenge_model.difficulty_from_severity`, N+10 —
`JudgeSeverity.LOW..CRITICAL` -> `1..4`) — jamais un second barème
arbitraire : `Difficulty` est une simple façade lisible (`EASY`..`EXPERT`)
sur ce MÊME entier, pas une nouvelle notion de difficulté.

Le `CurriculumEngine` ne doit jamais inventer un niveau : il ne peut que
demander « plus difficile que X » / « même difficulté » / « plus facile »
(`harder_than`/`same_difficulty_as`/`easier_than`, ci-dessous — comparaisons
pures sur l'entier, jamais une estimation), et toute borne passe par
`clamp_to_ceiling`, jamais un dépassement silencieux de la politique."""

from __future__ import annotations

from enum import IntEnum


class Difficulty(IntEnum):
    EASY = 1
    MEDIUM = 2
    HARD = 3
    EXPERT = 4


def from_int(value: int) -> Difficulty:
    if value not in (1, 2, 3, 4):
        raise ValueError(f"difficulty={value} hors bornes [1, 4]")
    return Difficulty(value)


def harder_than(a: Difficulty, b: Difficulty) -> bool:
    return int(a) > int(b)


def easier_than(a: Difficulty, b: Difficulty) -> bool:
    return int(a) < int(b)


def same_difficulty_as(a: Difficulty, b: Difficulty) -> bool:
    return int(a) == int(b)


def clamp_to_ceiling(requested: Difficulty, *, max_allowed: Difficulty | None) -> Difficulty:
    """Ne dépasse JAMAIS un plafond de politique réel — § Phase 7 de la
    mission N+11 : « la difficulté ne doit jamais dépasser les limites
    définies par policy »."""
    if max_allowed is None:
        return requested
    return requested if int(requested) <= int(max_allowed) else max_allowed
