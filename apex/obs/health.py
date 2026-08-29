"""Contrat de santé commun à tous les composants APEX (principe 13).

Chaque composant important expose `health() -> ComponentHealth`. L'agrégation
donne l'état système, consommé par `apex doctor`, l'API (phase 10) et le
Dashboard (phase 20).

Trois états seulement, et la distinction compte :

- `OK`        : le composant fait ce qu'il annonce.
- `DEGRADED`  : le composant fonctionne sur son repli. Le système reste utilisable ;
                c'est l'état normal d'APEX quand les services externes sont coupés.
- `FAILED`    : le composant ne peut pas rendre son service.

`DEGRADED` est un état de première classe et non une nuance de panne : le
fonctionnement 100 % local avec les fournisseurs externes indisponibles doit
apparaître comme dégradé-mais-sain, jamais comme une erreur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"

    @property
    def rank(self) -> int:
        return {"ok": 0, "degraded": 1, "failed": 2}[self.value]


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    status: Status
    detail: str = ""
    version: str | None = None
    fallback_active: bool = False
    latency_ms: float | None = None
    checked_at: float = field(default_factory=time.time)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "status": self.status.value,
            "detail": self.detail,
            "version": self.version,
            "fallback_active": self.fallback_active,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
            "facts": self.facts,
        }


@runtime_checkable
class HealthCheckable(Protocol):
    """Tout composant important d'APEX implémente ce protocole."""

    def health(self) -> ComponentHealth: ...


@dataclass(frozen=True)
class SystemHealth:
    status: Status
    components: tuple[ComponentHealth, ...]

    @property
    def degraded(self) -> tuple[ComponentHealth, ...]:
        return tuple(c for c in self.components if c.status is Status.DEGRADED)

    @property
    def failed(self) -> tuple[ComponentHealth, ...]:
        return tuple(c for c in self.components if c.status is Status.FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "components": [c.to_dict() for c in self.components],
        }


def aggregate(components: list[ComponentHealth]) -> SystemHealth:
    """L'état système est le pire état de ses composants."""
    if not components:
        return SystemHealth(status=Status.OK, components=())
    worst = max(components, key=lambda c: c.status.rank)
    return SystemHealth(status=worst.status, components=tuple(components))
