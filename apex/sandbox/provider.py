"""Contrat `SandboxProvider` — le seul point que connaissent `AgentRuntime`
et `apex.missions.execution`. Ajouter un backend (conteneur, seccomp, un
mécanisme plus fort demain) ne demande de modifier NI AgentRuntime NI
MissionEngine : seulement d'implémenter cette interface et de la brancher
dans `get_default_provider()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.sandbox.model import CancellationToken, SandboxCapabilityReport, SandboxSpec
from apex.sandbox.result import SandboxResult


class SandboxProvider(ABC):
    @abstractmethod
    def detect(self) -> SandboxCapabilityReport:
        """Sonde (ou relit un résultat mis en cache par l'implémentation) les
        mécanismes d'isolation réellement disponibles. Ne lève jamais."""

    @abstractmethod
    def execute(self, spec: SandboxSpec, *, cancel_token: CancellationToken | None = None) -> SandboxResult:
        """Exécute `spec.command` sous isolation. Lève `IsolationUnavailable`
        (apex.errors) si `detect().is_available` est faux — n'exécute JAMAIS
        en dégradé silencieux. Toujours nettoyée à la sortie, succès ou non.

        `cancel_token` (étape 8) permet à un appelant (le Scheduler) de
        demander une terminaison réelle depuis un autre thread pendant que
        cet appel bloque — chaque backend décide COMMENT tuer ce qu'il a
        démarré (`os.killpg` pour `LinuxNamespaceSandbox`, autre chose pour
        un futur `ContainerSandbox`), le token ne fait qu'orchestrer l'appel."""


def get_default_provider() -> SandboxProvider:
    """Seul point de construction utilisé par AgentRuntime/MissionEngine —
    c'est ICI, et seulement ici, qu'un futur second backend serait choisi
    (ex. sur la base d'un `SandboxCapabilityReport` préférant un conteneur
    durci quand il devient réellement disponible)."""
    from apex.sandbox.linux_namespace import LinuxNamespaceSandbox

    return LinuxNamespaceSandbox()
