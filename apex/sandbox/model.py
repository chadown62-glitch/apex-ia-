"""Modèle de domaine du Sandbox — dataclasses pures, aucune dépendance OS.

`IsolationLevel` répond à UNE question : quel mécanisme réel protège
l'exécution ? Ne jamais confondre avec `apex.obs.status.DataStatus`
(REAL/SIMULATED/UNKNOWN, "cette mesure est-elle réelle ?") ni avec
`apex.gate.model.GateStatus` (conformité d'une mesure d'environnement) —
trois vocabulaires distincts pour trois questions distinctes, même principe
que documenté dans `apex/obs/status.py`.

**Jamais "container-grade" ni "fully isolated" pour `NAMESPACED`** : ce
niveau signifie namespaces Linux + cgroups + abaissement de privilège réel
+ racine en lecture seule + réseau coupé — vérifié empiriquement réel sur
cette machine (voir `apex/sandbox/detect.py`), mais SANS jail complet du
filesystem (pas de `pivot_root` vers un rootfs minimal) ni seccomp-bpf.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from apex.config.schema import SandboxDefaults


class IsolationLevel(str, Enum):
    """Ordonné du plus faible au plus fort — jamais réordonné à la légère,
    des comparaisons (`>=`) en dépendront potentiellement plus tard."""

    #: Aucune isolation — exécution directe (c'est l'état actuel d'AgentRuntime.run()).
    NONE = "none"
    #: rlimits seuls (CPU/mémoire/PID du processus courant), sans namespace ni cgroup.
    RLIMIT_ONLY = "rlimit_only"
    #: Namespaces Linux (mount/pid/net/ipc/uts) + cgroups + privilège réellement
    #: abaissé (setuid, pas un remap uid cosmétique) + racine read-only + réseau
    #: coupé. C'est le niveau du seul backend implémenté à ce jour.
    NAMESPACED = "namespaced"
    #: Conteneur OCI (Docker/runc) avec image dédiée — RÉSERVÉ, aucun backend
    #: ne l'implémente : le démon Docker n'est pas joignable sur cette machine
    #: (voir apex/sandbox/detect.py). Ne jamais instancier ce niveau tant
    #: qu'aucun ContainerSandbox n'existe réellement.
    CONTAINERIZED = "containerized"
    #: NAMESPACED/CONTAINERIZED + seccomp-bpf (filtrage d'appels système) —
    #: RÉSERVÉ, aucune bibliothèque seccomp n'est disponible sur cette machine.
    HARDENED = "hardened"

    @property
    def is_real_isolation(self) -> bool:
        """Distingue "aucune garantie" de "au moins une garantie noyau réelle"."""
        return self in (
            IsolationLevel.NAMESPACED, IsolationLevel.CONTAINERIZED, IsolationLevel.HARDENED,
        )


class ExecutionStatus(str, Enum):
    """État final d'une exécution — jamais de dépassement silencieux (§5 de
    la consigne étape 7). Chaque valeur autre que SUCCESS/FAILED nécessite
    une preuve noyau distincte, documentée dans `apex/sandbox/linux_namespace.py`
    à côté de chaque site de détection ; sinon FAILED reste le résultat
    honnête plutôt qu'une classification devinée."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    OOM_KILLED = "oom_killed"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    PROCESS_LIMIT_EXCEEDED = "process_limit_exceeded"
    DISK_LIMIT_EXCEEDED = "disk_limit_exceeded"
    NETWORK_DENIED = "network_denied"
    PERMISSION_DENIED = "permission_denied"
    #: Étape 8 — annulation demandée explicitement (Scheduler.cancel_mission),
    #: jamais confondu avec RESOURCE_LIMIT_EXCEEDED : les deux peuvent
    #: partager le même signal noyau (SIGKILL) mais pas la même cause.
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ResourceLimits:
    """Limites d'UNE exécution — toujours issues de la configuration
    (`ApexConfig.sandbox`, elle-même pilotée par `config.yaml`/variables
    d'environnement), JAMAIS du matériel détecté sur la machine courante
    (voir la correction architecturale précédente, `HardwareThresholds`/
    `PoolStatus`). Un futur appelant (Mission Engine, une fois le champ
    exposé) pourra les surcharger par mission ; ce n'est pas encore le cas
    — voir la docstring de `apex.missions.execution`.
    """

    cpu_cores: float
    memory_mb: int
    pids_limit: int
    timeout_s: float
    disk_mb: int
    network: str  # "none" | "proxy" — même vocabulaire que SandboxDefaults.network

    @classmethod
    def from_config(cls, defaults: SandboxDefaults, *, disk_mb: int = 64) -> ResourceLimits:
        return cls(
            cpu_cores=defaults.cpu_limit,
            memory_mb=defaults.memory_mb,
            pids_limit=defaults.pids_limit,
            timeout_s=float(defaults.timeout_s),
            disk_mb=disk_mb,
            network=defaults.network,
        )


@dataclass(frozen=True)
class SandboxSpec:
    """Ce qu'on demande d'exécuter. `command` est un argv, jamais une
    fermeture Python : une vraie isolation OS exige un processus séparé
    (fork/exec réel), pas un objet marshalé à travers une frontière de
    privilège — contrairement à `AgentRuntime.run()`, qui reste
    délibérément intra-processus faute d'isolation (STUB documenté)."""

    mission_id: str
    clone_id: str
    command: tuple[str, ...]
    limits: ResourceLimits
    env: dict[str, str] = field(default_factory=dict)


class CancellationToken:
    """Poignée thread-safe entre l'appelant qui peut demander l'annulation
    (`apex.scheduler.service.SchedulerService.cancel_mission`) et le backend
    qui exécute réellement la commande (`LinuxNamespaceSandbox.execute`).

    Volontairement SANS logique de terminaison de processus ici : ce module
    reste sans dépendance OS (voir la docstring du fichier). C'est le
    backend qui, une fois le processus réellement démarré, fournit via
    `_register` la fonction qui sait le tuer et celle qui sait dire s'il est
    encore vivant — le token ne fait qu'orchestrer l'appel au bon moment,
    sans jamais supposer qu'un processus est mort sans l'avoir constaté.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._on_cancel: Callable[[], None] | None = None
        self._is_alive: Callable[[], bool] | None = None

    def _register(self, *, on_cancel: Callable[[], None], is_alive: Callable[[], bool]) -> None:
        """Appelé UNE fois par le backend, juste après le démarrage réel du
        processus — jamais par un appelant externe à `apex.sandbox`. Si
        l'annulation avait déjà été demandée avant cet enregistrement (course
        entre la décision d'annuler et le vrai démarrage), le kill est
        déclenché immédiatement : la fenêtre est fermée par le verrou, pas
        par un ordre d'appel supposé."""
        with self._lock:
            self._on_cancel = on_cancel
            self._is_alive = is_alive
            already_requested = self._cancel_requested
        if already_requested:
            on_cancel()

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            callback = self._on_cancel
        if callback is not None:
            callback()

    @property
    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def is_alive(self) -> bool | None:
        """État réellement constaté du processus, jamais deviné. `None` si
        aucun processus n'a encore été rattaché (avant démarrage, ou
        exécution jamais lancée) — distinct de `False` (constaté mort)."""
        with self._lock:
            check = self._is_alive
        return check() if check is not None else None

    def wait_until_terminated(self, *, timeout_s: float, poll_interval_s: float = 0.05) -> bool:
        """Bloque jusqu'à constater que le processus est réellement mort, ou
        jusqu'à `timeout_s`. Ne retourne jamais `True` sans l'avoir vérifié —
        c'est la preuve exigée par la consigne étape 8 ("vérifier que le
        groupe de processus est effectivement terminé"), pas une confiance
        aveugle dans le signal envoyé."""
        deadline = time.monotonic() + timeout_s
        alive = self.is_alive()
        if alive is None:
            return False
        while alive and time.monotonic() < deadline:
            time.sleep(poll_interval_s)
            alive = self.is_alive()
        return alive is False


@dataclass(frozen=True)
class SandboxCapabilityReport:
    """Résultat d'une détection EMPIRIQUE (jamais supposée) des mécanismes
    d'isolation réellement disponibles sur CETTE machine, à cet instant.
    Ne dit rien sur une autre machine — voir `apex.config.schema.PoolStatus`
    pour la distinction CURRENT_ENVIRONMENT / TARGET_ENVIRONMENT."""

    level: IsolationLevel
    namespaces_available: bool
    cgroups_available: bool
    privilege_drop_available: bool
    network_isolation_available: bool
    docker_daemon_reachable: bool
    seccomp_available: bool
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        """Le seuil minimal pour autoriser une exécution réelle : au moins
        NAMESPACED. RLIMIT_ONLY/NONE ne suffisent jamais — voir §2 de la
        consigne étape 7 : "si l'isolation forte n'est pas disponible,
        REFUSER l'exécution réelle"."""
        return self.level.is_real_isolation
