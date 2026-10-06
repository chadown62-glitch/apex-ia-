"""Contrat d'un outil APEX : identité, description, schémas d'entrée/sortie, permissions, limites, résultat
observable, erreur structurée, provenance. Aucune permission n'est jamais implicite : un appel n'est exécuté que
si le contexte lui accorde EXPLICITEMENT chacune des permissions déclarées par l'outil."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Permission(str, Enum):
    #: Lire un fichier ou lister un répertoire DE L'ESPACE DE TRAVAIL (jamais au-delà).
    READ_WORKSPACE = "workspace.read"
    #: Créer ou modifier un fichier de l'espace de travail.
    WRITE_WORKSPACE = "workspace.write"
    #: Exécuter du code dans la sandbox (réseau coupé, limites CPU/mémoire/temps), sur une COPIE de l'espace.
    EXECUTE_SANDBOX = "sandbox.execute"


class ErrorCode(str, Enum):
    UNKNOWN_TOOL = "unknown_tool"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INPUT = "invalid_input"
    PATH_REFUSED = "path_refused"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class ToolLimits:
    #: Délai maximal d'une exécution sandbox lancée par l'outil (secondes).
    timeout_s: float = 30.0
    #: Taille maximale des arguments de l'appel (JSON, octets).
    max_input_bytes: int = 200_000
    #: Taille maximale de chaque texte rendu dans la sortie (caractères) ; au-delà : tronqué ET signalé.
    max_output_chars: int = 20_000


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: frozenset[Permission]
    limits: ToolLimits = field(default_factory=ToolLimits)

    def describe(self) -> dict[str, Any]:
        """Ce qui est montré au modèle : identité, description, schéma d'entrée — jamais d'implémentation."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    call_id: str
    #: Qui demande l'appel (provenance) : ex. `taskloop:<run>:<tentative>` ou `api:human`.
    requested_by: str


@dataclass(frozen=True)
class ToolError:
    code: ErrorCode
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message}


class ToolFailure(Exception):
    """Levée par une implémentation d'outil pour une erreur ATTENDUE (chemin refusé, fichier absent, ...) :
    convertie par le moteur en `ToolError` structurée, jamais propagée."""

    def __init__(self, code: ErrorCode, message: str, *, output: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.output = output


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool: str
    ok: bool
    output: dict[str, Any] | None
    error: ToolError | None
    duration_s: float
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id, "tool": self.tool, "ok": self.ok, "output": self.output,
            "error": self.error.to_dict() if self.error else None, "duration_s": round(self.duration_s, 4),
            "provenance": self.provenance,
        }
