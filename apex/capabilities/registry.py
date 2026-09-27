"""Registre des capacités apprenables et accès au sandbox d'évaluation.

Ajouter une capacité = implémenter `Capability` et l'inscrire ici ; la boucle
autonome, le monde et le Judge n'ont pas à changer."""

from __future__ import annotations

from functools import cache

from apex.capabilities.contract import Capability
from apex.errors import IsolationUnavailable
from apex.sandbox.provider import SandboxProvider, select_sandbox_provider

AVAILABLE = ("code_repair", "program_trace", "dependency_order")


def get_capability(name: str) -> Capability:
    if name == "code_repair":
        from apex.capabilities.code_repair.capability import CodeRepairCapability

        return CodeRepairCapability()
    if name == "program_trace":
        from apex.capabilities.program_trace.capability import ProgramTraceCapability

        return ProgramTraceCapability()
    if name == "dependency_order":
        from apex.capabilities.dependency_order.capability import DependencyOrderCapability

        return DependencyOrderCapability()
    raise KeyError(f"capacité inconnue « {name} » (disponibles : {AVAILABLE})")


@cache
def sandbox_provider() -> SandboxProvider:
    """Sandbox réel de l'hôte, sélectionné une fois. Jamais d'exécution en
    dégradé : sans isolation vérifiée, l'évaluation d'une capacité échoue."""
    selection = select_sandbox_provider()
    if not selection.report.is_available:
        raise IsolationUnavailable(
            "évaluation de capacité", required="namespaced", available=selection.report.level.value
        )
    return selection.provider
