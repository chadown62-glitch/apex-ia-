"""Moteur d'outils : registre + exécution contrôlée d'un appel.

Ordre des contrôles (chacun fail-closed, AVANT toute exécution) : outil connu -> permissions accordées
EXPLICITEMENT par le contexte (jamais implicites) -> taille des arguments -> schéma d'entrée. Ensuite seulement
l'outil s'exécute ; toute exception est convertie en erreur structurée (une erreur attendue garde son code, une
erreur inattendue devient `internal_error` avec son type). Chaque appel est journalisé (`tool.called`) avec sa
provenance : outil, appelant, espace de travail, empreinte des arguments, durée, résultat.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apex.tools.model import ErrorCode, Permission, ToolCall, ToolError, ToolFailure, ToolResult, ToolSpec
from apex.tools.schema import check_schema, validate
from apex.tools.workspace import Workspace

#: Implémentation d'un outil : (arguments validés, contexte, spécification) -> sortie (dict JSON-sérialisable).
Handler = Callable[[dict[str, Any], "ToolContext", ToolSpec], dict[str, Any]]


@dataclass
class ToolContext:
    """Ce qu'un appel a le droit d'utiliser. `run_in_sandbox` est fourni par l'appelant (le TaskLoop le
    construit sur `AgentRuntime.execute_in_sandbox`, autorisation PolicyEngine comprise) — le moteur ne crée
    jamais lui-même un moyen d'exécution."""

    workspace: Workspace
    granted: frozenset[Permission]
    run_in_sandbox: Callable[[Any], Any] | None = None
    emit: Callable[[str, dict[str, Any]], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Handler]] = {}

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"outil « {spec.name} » déjà enregistré")
        check_schema(spec.input_schema)
        check_schema(spec.output_schema)
        self._tools[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[ToolSpec, Handler] | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    def available_to(self, granted: frozenset[Permission]) -> list[ToolSpec]:
        """Outils dont TOUTES les permissions sont accordées — ce que l'on peut proposer au modèle."""
        return [spec for spec in self.specs() if spec.permissions <= granted]


class ToolEngine:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        provenance: dict[str, Any] = {
            "requested_by": call.requested_by, "workspace_id": context.workspace.id,
            "arguments_sha256": _digest(call.arguments),
        }
        entry = self.registry.get(call.tool)
        if entry is None:
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.UNKNOWN_TOOL, f"outil « {call.tool} » inconnu"))
        spec, handler = entry
        missing = sorted(p.value for p in spec.permissions - context.granted)
        if missing:
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.PERMISSION_DENIED, f"permission(s) non accordée(s) : {', '.join(missing)}"))
        try:
            size = len(json.dumps(call.arguments, ensure_ascii=False).encode())
        except (TypeError, ValueError):
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.INVALID_INPUT, "arguments non sérialisables en JSON"))
        if size > spec.limits.max_input_bytes:
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.TOO_LARGE, f"arguments trop volumineux ({size} octets)"))
        violations = validate(call.arguments, spec.input_schema)
        if violations:
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.INVALID_INPUT, "; ".join(violations[:10])))
        try:
            output = handler(call.arguments, context, spec)
        except ToolFailure as failure:
            return self._finish(call, context, started, provenance, output=failure.output,
                                error=ToolError(failure.code, failure.message))
        except Exception as exc:  # erreur inattendue : jamais propagée, jamais masquée
            return self._finish(call, context, started, provenance, error=ToolError(
                ErrorCode.INTERNAL_ERROR, f"{exc.__class__.__name__}: {exc}"[:500]))
        return self._finish(call, context, started, provenance, output=output)

    def _finish(
        self, call: ToolCall, context: ToolContext, started: float, provenance: dict[str, Any], *,
        output: dict[str, Any] | None = None, error: ToolError | None = None,
    ) -> ToolResult:
        result = ToolResult(
            call_id=call.call_id, tool=call.tool, ok=error is None, output=output, error=error,
            duration_s=time.perf_counter() - started, provenance=provenance,
        )
        if context.emit is not None:
            context.emit("tool.called", {
                "call_id": call.call_id, "tool": call.tool, "ok": result.ok,
                "error": error.to_dict() if error else None, "duration_s": round(result.duration_s, 4),
                **provenance,
            })
        return result


def _digest(arguments: Any) -> str:
    try:
        raw = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=repr).encode()
    except (TypeError, ValueError):
        raw = repr(arguments).encode()
    return hashlib.sha256(raw).hexdigest()
