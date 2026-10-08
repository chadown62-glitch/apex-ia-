"""Processus et verrou d'instance pour APEX autonome — Windows (API Win32 par ctypes) et POSIX.

Le Panel et `python -m apex.standalone stop` doivent savoir si le backend enregistré vit, attendre sa fin,
vérifier QUEL programme porte un pid avant d'y toucher, et n'avoir qu'un démarrage à la fois. Sous Windows,
ni `/proc`, ni signaux entre processus : `OpenProcess`/`GetExitCodeProcess`/`QueryFullProcessImageNameW`/
`TerminateProcess`, et un verrou de fichier `msvcrt.locking`. Aucune dépendance tierce.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import IO, Any

_WINDOWS = sys.platform == "win32"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259
_WAIT_OBJECT_0 = 0


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    return k32


@contextlib.contextmanager
def _open(pid: int, access: int):  # type: ignore[no-untyped-def]
    k32 = _kernel32()
    handle = k32.OpenProcess(access, False, pid)
    try:
        yield (k32, handle)
    finally:
        if handle:
            k32.CloseHandle(handle)


def alive(pid: int) -> bool:
    if pid < 1:
        return False
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        with _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION) as (k32, handle):
            if not handle:
                return False
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Un processus terminé mais pas encore récolté par son parent (zombie) n'exécute plus rien : terminé.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


def wait_exit(pid: int, timeout_s: float) -> bool:
    """Vrai si le processus est constaté terminé dans le délai (jamais supposé)."""
    if _WINDOWS:
        with _open(pid, _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION) as (k32, handle):
            if not handle:
                return not alive(pid)
            return bool(k32.WaitForSingleObject(handle, int(timeout_s * 1000)) == _WAIT_OBJECT_0)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


def image_path(pid: int) -> str | None:
    """Chemin de l'exécutable qui porte ce pid — pour vérifier qu'il s'agit bien du Python d'APEX."""
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        with _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION) as (k32, handle):
            if not handle:
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def same_executable(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        left, right = Path(a).resolve(), Path(b).resolve()
    except OSError:
        return False
    return str(left).lower() == str(right).lower() if _WINDOWS else left == right


def terminate(pid: int) -> bool:
    """Arrêt FORCÉ — seulement après une demande d'arrêt propre restée sans effet."""
    if _WINDOWS:
        with _open(pid, _PROCESS_TERMINATE | _SYNCHRONIZE) as (k32, handle):
            if not handle:
                return False
            return bool(k32.TerminateProcess(handle, 1))
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return True


class SingleInstanceLock:
    """Un seul démarrage d'APEX à la fois par utilisateur : verrou exclusif NON bloquant sur un fichier,
    tenu toute la vie du processus et libéré par le système à sa fin, même brutale."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 — gardé ouvert : c'est le verrou
        try:
            if _WINDOWS:
                import msvcrt

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
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            if _WINDOWS:
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
