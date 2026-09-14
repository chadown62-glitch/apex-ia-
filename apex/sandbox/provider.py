"""Contrat `SandboxProvider` — le seul point que connaissent `AgentRuntime`
et `apex.missions.execution`. Ajouter un backend (conteneur, seccomp, un
mécanisme plus fort demain) ne demande de modifier NI AgentRuntime NI
MissionEngine : seulement d'implémenter cette interface et de la brancher
dans `select_sandbox_provider()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from apex.sandbox.model import CancellationToken, IsolationLevel, SandboxCapabilityReport, SandboxSpec
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
        démarré (`os.killpg` pour `LinuxNamespaceSandbox`, `docker kill` pour
        `ContainerSandbox`), le token ne fait qu'orchestrer l'appel."""


@dataclass(frozen=True)
class SandboxSelection:
    """Explique QUEL backend a été retenu et POURQUOI — l'exigence
    d'observabilité de la sélection (jamais un choix opaque, jamais un
    conteneur choisi au seul motif que `docker` est installé). `report` est
    le `SandboxCapabilityReport` du backend RETENU (le seul qui compte pour
    la suite : `provider.detect()` renverra ce même rapport, déjà mis en
    cache — aucune re-sonde)."""

    provider: SandboxProvider
    provider_name: str
    report: SandboxCapabilityReport
    reason: str


def select_sandbox_provider(
    *, container_image: str | None = None, namespace_report: SandboxCapabilityReport | None = None,
) -> SandboxSelection:
    """Sélection EXPLICITE, dans un ordre fixe, jamais devinée :

    1. `LinuxNamespaceSandbox` si NAMESPACED est réellement disponible sur cet
       hôte (namespaces + cgroups v1 + privilège abaissé + réseau coupé, tous
       vérifiés empiriquement par `apex.sandbox.detect`) — aucune dépendance
       externe à un démon tiers, donc préféré quand il fonctionne.
    2. Sinon `ContainerSandbox`, SEULEMENT si Docker est réellement joignable
       ET si la sonde conteneur réelle (`apex.sandbox.container.
       detect_container_capabilities`) confirme empiriquement les garanties
       de sécurité (réseau coupé, utilisateur non-root, racine lecture
       seule) — jamais choisi au seul motif que le binaire `docker` existe.
    3. Sinon aucun backend disponible : `LinuxNamespaceSandbox` est quand même
       retourné (c'est lui qui porte le message d'échec le plus détaillé via
       `detect()`), mais son `execute()` lèvera `IsolationUnavailable` — la
       sélection ne fabrique jamais un backend fonctionnel qui n'existe pas.

    `namespace_report`/`container_image` sont injectables — seulement pour
    les tests (simuler une machine RLIMIT_ONLY sans changer la machine
    réelle), même patron que `capability_report` sur chaque backend.
    """
    from apex.sandbox.container import ContainerSandbox, detect_container_capabilities
    from apex.sandbox.detect import detect_capabilities
    from apex.sandbox.linux_namespace import LinuxNamespaceSandbox

    ns_report = namespace_report if namespace_report is not None else detect_capabilities()
    if ns_report.level is IsolationLevel.NAMESPACED:
        return SandboxSelection(
            provider=LinuxNamespaceSandbox(capability_report=ns_report),
            provider_name="LinuxNamespaceSandbox",
            report=ns_report,
            reason=(
                "namespaces Linux + cgroups v1 + privilège réellement abaissé + réseau "
                f"coupé, tous vérifiés empiriquement sur cet hôte ({ns_report.detail}) — "
                "NAMESPACED est disponible et préféré (aucune dépendance à un démon tiers)."
            ),
        )

    resolved_image = container_image
    if resolved_image is None:
        from apex.config.loader import load_config

        resolved_image = load_config().sandbox.container_image

    container_report = detect_container_capabilities(image=resolved_image)
    if container_report.is_available:
        return SandboxSelection(
            provider=ContainerSandbox(image=resolved_image, capability_report=container_report),
            provider_name="ContainerSandbox",
            report=container_report,
            reason=(
                "namespaces Linux indisponibles sur cet hôte "
                f"({ns_report.detail}) ; repli sur Docker — garanties de sécurité "
                "(réseau coupé, utilisateur non-root, racine en lecture seule) vérifiées "
                f"empiriquement sur un vrai conteneur ({container_report.detail})."
            ),
        )

    return SandboxSelection(
        provider=LinuxNamespaceSandbox(capability_report=ns_report),
        provider_name="LinuxNamespaceSandbox",
        report=ns_report,
        reason=(
            "aucun backend d'isolation réellement disponible sur cet hôte : "
            f"namespaces Linux ({ns_report.detail}) ET Docker "
            f"({container_report.detail}) tous deux insuffisants. `execute()` lèvera "
            "IsolationUnavailable — jamais une exécution dégradée silencieuse."
        ),
    )


def get_default_provider() -> SandboxProvider:
    """Seul point de construction utilisé par AgentRuntime/MissionEngine —
    délègue entièrement à `select_sandbox_provider()`, la seule source de
    vérité sur QUEL backend est choisi et POURQUOI. Les appelants qui ont
    besoin de la justification (diagnostic, `apex doctor`, tests) appellent
    `select_sandbox_provider()` directement plutôt que cette façade."""
    return select_sandbox_provider().provider
