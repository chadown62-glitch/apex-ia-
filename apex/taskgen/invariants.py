"""Invariants de séparation de dataset (passe « Universal Task + Curriculum
Engine », N+11, § Phase 15/22 de la mission) — fonctions PURES, utilisées
par les tests pour vérifier des ensembles d'identifiants RÉELLEMENT
persistés, jamais un contrôle runtime supplémentaire qui dupliquerait la
séparation déjà structurelle (`apex.tasks.model.TaskSplit` + l'absence de
GRANT sur `tasks` pour `apex_eval`, ADR-0010 — inchangée)."""

from __future__ import annotations

from collections.abc import Iterable


def disjoint(a: Iterable[str], b: Iterable[str]) -> bool:
    return not (set(a) & set(b))


def assert_disjoint(a: Iterable[str], b: Iterable[str], *, label: str) -> None:
    overlap = set(a) & set(b)
    if overlap:
        raise ValueError(f"{label} : chevauchement détecté sur {sorted(overlap)}")


def assert_unique_ids(ids: Iterable[str], *, label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in ids:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    if duplicates:
        raise ValueError(f"{label} : identifiants dupliqués {sorted(duplicates)}")
