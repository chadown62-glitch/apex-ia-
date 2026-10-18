"""Partage du GPU entre les modèles principaux d'APEX (Cerveau APEX, moteur intégré) et les spécialistes
(Game Studio : planificateur, Blender…), dans le processus propriétaire des modèles.

Protocole (Issue #21, accepté par les deux côtés) :

1. **Bail partagé.** Tant qu'un modèle principal est chargé sur CUDA, ce processus TIENT le bail
   `APEX_HOME/.apex/model-session.lock` : même fichier et même primitive que
   `apex.game_graphics.resources._locked` (verrou exclusif non bloquant : `flock` sous POSIX,
   `msvcrt.locking` sur un octet sous Windows). L'acquisition est atomique (pas de contrôle préalable, donc
   pas de TOCTOU). Si le bail est tenu ailleurs, le modèle se charge sur le processeur, et ses mesures le
   disent.
2. **Demande du spécialiste** : `request_gpu(timeout_s, requester)`, dans ce processus. Le spécialiste
   n'appelle jamais un déchargement lui-même. Dans l'ordre :
   - les nouvelles admissions CUDA sont refusées (les nouvelles générations attendent la fin de la
     transition, puis tournent sur le processeur) ;
   - les générations en cours se terminent, sans être interrompues, dans le délai ;
   - les modèles CUDA sont déchargés et la mémoire CUDA est mesurée avant et après ;
   - le bail est libéré, et un accusé de réception est rendu.

   Délai dépassé : rien n'est déchargé, les admissions sont rouvertes, et l'accusé dit
   `released: false`. Le spécialiste garde alors sa mission en attente (BLOCKED/queued). Aucun processus
   n'est tué, et il n'y a jamais de bascule silencieuse.
3. **Reprise** : `return_gpu(requester)` rend LE prêt de ce demandeur. Le prochain chargement reprend le
   bail s'il est libre.
4. **Prêts comptés** (`PROTOCOL_VERSION` 2, demandé par Codex dans l'Issue #21) : seule une demande
   acceptée est comptée, par demandeur (`lenders`, `{demandeur: nombre}`). Une demande pendant un prêt est
   accordée sans attente, puisque rien n'a pu se charger sur CUDA depuis. Un retour :
   - d'un demandeur sans prêt : `returned: false`, et rien ne change ;
   - d'un demandeur connu : un prêt de moins, `returned: true` ; `resumed: true` seulement si c'était le
     dernier, et les admissions rouvrent alors.
   `lent_to` reste un texte d'affichage (le demandeur seul, ou la liste) : un programme lit `lenders`. Une
   demande alors que rien n'est sur CUDA (ni bail ni modèle) n'attend pas les générations sur le
   processeur.

Ce mécanisme est coopératif, comme le bail de Game Studio : il ne contrôle pas les programmes extérieurs à
APEX. Aucune mesure CUDA n'a été faite sans GPU : sans CUDA, l'accusé le dit (`cuda.measured: false`)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

LEASE_NAME = "model-session.lock"
#: Version du protocole de prêt (2 : prêts comptés par demandeur, retour d'un inconnu sans effet).
PROTOCOL_VERSION = 2
#: Bornes du délai de quiescence demandé par un spécialiste (secondes).
QUIESCE_LIMITS = (0.1, 3600.0)


def lease_path() -> Path | None:
    """Le bail partagé ; None sans profil APEX absolu (Game Studio refuse alors lui-même de démarrer :
    `shared_resource_profile_required`)."""
    home = os.environ.get("APEX_HOME")
    if not home or not Path(home).is_absolute():
        return None
    return Path(home) / ".apex" / LEASE_NAME


class SharedLease:
    """Le bail exclusif, tenu tant que l'objet le détient. Même primitive que Game Studio."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class TransitionTimeout(TimeoutError):
    """Une génération a attendu la fin d'une transition du GPU au-delà de son délai."""


@dataclass
class Resident:
    """Un modèle principal chargé sur CUDA : son verrou de génération et son déchargement."""

    name: str
    lock: threading.Lock
    unload: Callable[[], None]


def _cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _cuda_memory() -> dict[str, int] | None:
    """Mémoire CUDA de ce processus (torch) et du périphérique ; None sans CUDA."""
    if not _cuda_available():
        return None
    import torch

    torch.cuda.synchronize()
    free, total = torch.cuda.mem_get_info()
    return {"allocated": int(torch.cuda.memory_allocated()), "reserved": int(torch.cuda.memory_reserved()),
            "device_free": int(free), "device_total": int(total)}


@dataclass
class GpuGovernor:
    """Admissions CUDA des modèles principaux de ce processus. Un seul par processus (`governor()`) ;
    les tests en construisent avec leurs propres sondes."""

    lease_path: Callable[[], Path | None] = lease_path
    cuda_available: Callable[[], bool] = _cuda_available
    cuda_memory: Callable[[], dict[str, int] | None] = _cuda_memory
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _open: bool = True
    _quiescing: bool = False
    _active: int = 0
    _lease: SharedLease | None = None
    _runtimes: dict[str, Callable[[], list[Resident]]] = field(default_factory=dict)
    _last_ack: dict[str, Any] | None = None
    #: Prêts en cours : demandeur → nombre de demandes acceptées non rendues.
    _lenders: dict[str, int] = field(default_factory=dict)

    # -- côté modèles principaux ------------------------------------------------------------------------

    def register(self, runtime: str, residents: Callable[[], list[Resident]]) -> None:
        """Un runtime déclare comment énumérer ses modèles chargés sur CUDA."""
        with self._cond:
            self._runtimes[runtime] = residents

    @contextmanager
    def generation(self, *, deadline: float | None = None) -> Iterator[None]:
        """Une génération d'un modèle principal. Pendant une transition, elle attend qu'elle se termine
        (jusqu'à `deadline`, horloge `time.monotonic`) ; elle est comptée tant qu'elle dure, et
        `request_gpu` attend qu'elle se termine."""
        with self._cond:
            while self._quiescing:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TransitionTimeout("transition du GPU vers un spécialiste en cours")
                self._cond.wait(timeout=remaining)
            self._active += 1
        try:
            yield
        finally:
            with self._cond:
                self._active -= 1
                self._cond.notify_all()

    def place(self, requested: str = "auto") -> tuple[str, str]:
        """Où charger un modèle principal : (`cuda` | `cpu`, raison). CUDA seulement si demandé (ou
        automatique), disponible, admissions ouvertes et bail obtenu ; le bail est alors TENU jusqu'au
        déchargement du dernier modèle CUDA."""
        if requested == "cpu":
            return "cpu", "processeur demandé"
        if not self.cuda_available():
            return "cpu", "aucun GPU CUDA"
        with self._cond:
            if not self._open:
                return "cpu", f"GPU prêté à un spécialiste ({self._lent_to() or 'inconnu'})"
            path = self.lease_path()
            if path is None:
                return "cuda", "sans profil APEX partagé : bail non utilisé"
            if self._lease is None or self._lease.path != path:
                self._lease = SharedLease(path)
            if self._lease.try_acquire():
                return "cuda", "bail GPU tenu par les modèles principaux"
            return "cpu", "GPU tenu par un autre travail (bail model-session.lock)"

    def unloaded(self) -> None:
        """Un runtime a déchargé un modèle (inactivité, configuration) : le bail est rendu s'il ne reste
        aucun modèle CUDA ni génération en cours."""
        with self._cond:
            if self._lease is not None and self._lease.held and self._active == 0 and not self._residents():
                self._lease.release()

    # -- côté spécialistes -----------------------------------------------------------------------------

    def request_gpu(self, timeout_s: float, *, requester: str) -> dict[str, Any]:
        """Quiescence : voir le protocole en tête du module. Rend l'accusé de réception."""
        timeout = min(max(float(timeout_s), QUIESCE_LIMITS[0]), QUIESCE_LIMITS[1])
        started = time.monotonic()
        with self._cond:
            if self._quiescing:
                return self._ack(False, requester, started, "une transition est déjà en cours", [])
            if not self._open and self._lenders:
                # Déjà prêté : les admissions CUDA sont fermées depuis, rien n'a pu s'y charger.
                self._lend(requester)
                return self._ack(True, requester, started, f"GPU déjà prêté ({self._lent_to()})", [])
            if not (self._lease is not None and self._lease.held) and not self._residents():
                # Rien sur CUDA : fermer les admissions suffit (les générations sur le processeur
                # continuent ; toute génération qui aurait obtenu CUDA tiendrait le bail).
                self._open = False
                self._lend(requester)
                return self._ack(True, requester, started, "aucun modèle principal sur CUDA", [])
            self._quiescing, self._open = True, False
            deadline = started + timeout
            while self._active > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._quiescing, self._open = False, True  # rien n'est déchargé : tout reprend
                    self._cond.notify_all()
                    return self._ack(False, requester, started,
                                     f"génération en cours au-delà du délai de {timeout:g} s", [])
                self._cond.wait(timeout=remaining)
            residents = self._residents()
            taken: list[Resident] = []
            try:
                # Aucune génération admise : chaque verrou est libre ou se libère.
                for resident in residents:
                    if not resident.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                        self._open = True
                        return self._ack(False, requester, started,
                                         f"{resident.name} encore occupé au-delà du délai", [])
                    taken.append(resident)
                before = self.cuda_memory()
                for resident in residents:
                    resident.unload()
                after = self.cuda_memory()
                if self._lease is not None:
                    self._lease.release()
                self._lend(requester)
            except Exception as exc:  # déchargement en échec : rien n'est prétendu libéré
                self._open = True
                return self._ack(False, requester, started, f"déchargement impossible : {exc}"[:300], [])
            finally:
                for resident in taken:
                    resident.lock.release()
                self._quiescing = False
                self._cond.notify_all()
            return self._ack(True, requester, started, "GPU libéré par les modèles principaux",
                             [r.name for r in residents], before, after)

    def return_gpu(self, *, requester: str) -> dict[str, Any]:
        """Le spécialiste rend SON prêt : sans prêt à ce nom, rien ne change (`returned: false`) ; sinon un
        prêt de moins (`returned: true`), et au dernier les admissions CUDA rouvrent (`resumed: true` ; le
        bail sera repris au prochain chargement s'il est libre). Une transition n'a lieu que sans prêt en
        cours : un retour pendant une transition est donc celui d'un inconnu."""
        with self._cond:
            previous = self._lent_to()
            count = self._lenders.get(requester, 0)
            if count == 0:
                return self._returned(False, False, requester, previous, "aucun prêt à ce demandeur")
            if count > 1:
                self._lenders[requester] = count - 1
            else:
                del self._lenders[requester]
            resumed = not self._lenders
            if resumed:
                self._open = True
                self._cond.notify_all()
            return self._returned(True, resumed, requester, previous,
                                  None if resumed else f"GPU encore prêté ({self._lent_to()})")

    def status(self) -> dict[str, Any]:
        with self._cond:
            return {"protocol_version": PROTOCOL_VERSION, "admissions_open": self._open,
                    "transition": self._quiescing, "active_generations": self._active,
                    "lease_held": bool(self._lease and self._lease.held),
                    "lease_path": str(self.lease_path() or "") or None, "lent_to": self._lent_to(),
                    "lenders": dict(self._lenders), "cuda_residents": [r.name for r in self._residents()],
                    "cuda_available": self.cuda_available(), "last_ack": self._last_ack}

    # -- interne ---------------------------------------------------------------------------------------

    def _lend(self, requester: str) -> None:
        self._lenders[requester] = self._lenders.get(requester, 0) + 1

    def _lent_to(self) -> str | None:
        return ", ".join(self._lenders) or None

    def _returned(self, returned: bool, resumed: bool, requester: str, previous: str | None,
                  reason: str | None) -> dict[str, Any]:
        answer = {"returned": returned, "resumed": resumed, "requester": requester, "lent_to": previous,
                  "lenders": dict(self._lenders), "admissions_open": self._open,
                  "protocol_version": PROTOCOL_VERSION}
        return answer if reason is None else {**answer, "reason": reason}

    def _residents(self) -> list[Resident]:
        found: list[Resident] = []
        for residents in self._runtimes.values():
            found.extend(residents())
        return found

    def _ack(self, released: bool, requester: str, started: float, reason: str, unloaded: list[str],
             before: dict[str, int] | None = None, after: dict[str, int] | None = None) -> dict[str, Any]:
        cuda: dict[str, Any]
        if before is None or after is None:
            why = ("aucun GPU CUDA dans ce processus" if not self.cuda_available()
                   else "non mesurée (rien n'a été déchargé)")
            cuda = {"measured": False, "reason": why}
        else:
            cuda = {"measured": True, "before": before, "after": after,
                    "freed_allocated": before["allocated"] - after["allocated"],
                    "freed_reserved": before["reserved"] - after["reserved"]}
        ack = {"released": released, "requester": requester, "reason": reason, "unloaded": unloaded,
               "waited_s": round(time.monotonic() - started, 3), "admissions_open": self._open,
               "lease_held": bool(self._lease and self._lease.held),
               "lease_path": str(self.lease_path() or "") or None, "lenders": dict(self._lenders),
               "protocol_version": PROTOCOL_VERSION, "cuda": cuda}
        self._last_ack = ack
        return ack


_GOVERNOR: GpuGovernor | None = None
_GOVERNOR_LOCK = threading.Lock()


def governor() -> GpuGovernor:
    global _GOVERNOR
    with _GOVERNOR_LOCK:
        if _GOVERNOR is None:
            _GOVERNOR = GpuGovernor()
        return _GOVERNOR


def request_gpu(timeout_s: float, *, requester: str) -> dict[str, Any]:
    """Point d'entrée des spécialistes : demander le GPU aux modèles principaux (voir le module)."""
    return governor().request_gpu(timeout_s, requester=requester)


def return_gpu(*, requester: str) -> dict[str, Any]:
    """Point d'entrée des spécialistes : rendre le GPU aux modèles principaux."""
    return governor().return_gpu(requester=requester)


def gpu_status() -> dict[str, Any]:
    return governor().status()
