"""`WindowsAppContainerSandbox` — isolation NATIVE de Windows pour le code exécuté par APEX autonome (`APEX.exe`).

Mécanismes (API Win32, par ctypes, aucune dépendance tierce) :

- **AppContainer** (`CreateAppContainerProfile` + `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, AUCUNE
  capacité) : le processus n'a ni réseau (Internet, réseau privé et boucle locale coupés par défaut), ni accès
  aux fichiers de l'utilisateur ; il ne lit que ce qui lui est explicitement accordé (le Python d'APEX, en
  lecture/exécution) et n'écrit que dans son dossier de travail (accordé pour CETTE exécution) ;
- **Job Object** : mémoire du processus plafonnée, UN seul processus actif (aucun sous-processus), interface
  utilisateur restreinte, tué à la fermeture du job (dépassement de délai, annulation, fin d'APEX) ;
- **héritage de descripteurs restreint** (`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`) : seuls stdin/stdout/stderr.

**Jamais supposé** : `detect()` exécute une SONDE dans un vrai AppContainer et ne déclare l'isolation
disponible que si chaque garantie y est CONSTATÉE — connexion Internet refusée, connexion locale refusée,
fichier secret de l'utilisateur illisible, écriture hors du dossier de travail refusée, écriture dedans
possible, lancement d'un sous-processus refusé, allocation au-delà de la limite refusée. Une seule garantie
non constatée (ou une API absente : Windows trop ancien, Wine) : indisponible, avec la raison, et
`execute()` lève `IsolationUnavailable` — aucune exécution en dégradé.

Niveau : `IsolationLevel.APPCONTAINER` — comparable à NAMESPACED (isolation du système + réseau coupé +
limites), JAMAIS présenté comme un conteneur durci : ni filtrage d'appels système, ni noyau séparé ;
le Red Team reste désactivé.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from apex.errors import IsolationUnavailable
from apex.obs.status import Measurement
from apex.sandbox.model import (
    CancellationToken,
    ExecutionStatus,
    IsolationLevel,
    SandboxCapabilityReport,
    SandboxSpec,
)
from apex.sandbox.provider import SandboxProvider
from apex.sandbox.result import SandboxResult

PROFILE_NAME = "APEX.Sandbox"
_MAX_CAPTURE_BYTES = 1_000_000
_PROBE_MEMORY_MB = 256
_PROBE_TIMEOUT_S = 60.0
_PYTHON_ALIASES = frozenset({"python3", "python", "python3.12"})

# -- constantes Win32 ------------------------------------------------------------------------------------------
_HRESULT_ALREADY_EXISTS = 0x800700B7
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_HANDLE_FLAG_INHERIT = 0x00000001
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_JOB_BASIC_ACCOUNTING = 1
_JOB_BASIC_UI_RESTRICTIONS = 4
_JOB_EXTENDED_LIMIT = 9
_LIMIT_ACTIVE_PROCESS = 0x00000008
_LIMIT_PROCESS_MEMORY = 0x00000100
_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_UILIMIT_ALL = 0x000000FF


@functools.lru_cache(maxsize=1)
def _win32() -> Any:
    """Prototypes Win32 (64 bits). Lève OSError/AttributeError hors Windows ou si une API manque."""
    import ctypes
    from ctypes import wintypes

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
                    ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    userenv = ctypes.WinDLL("userenv", use_last_error=True)  # type: ignore[attr-defined]

    userenv.CreateAppContainerProfile.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                  ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    k32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
                               ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.DWORD]
    k32.CreatePipe.restype = wintypes.BOOL
    k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    k32.SetHandleInformation.restype = wintypes.BOOL
    k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                                      ctypes.POINTER(ctypes.c_size_t)]
    k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                              ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k32.DeleteProcThreadAttributeList.restype = None
    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.CreateProcessW.restype = wintypes.BOOL
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.ResumeThread.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL

    ns = dict(locals())
    return type("Win32", (), ns)


def _error(api: str) -> OSError:
    import ctypes

    code = ctypes.get_last_error()  # type: ignore[attr-defined]
    return OSError(code, f"{api} : erreur Win32 {code}")


def appcontainer_sid(name: str = PROFILE_NAME) -> str:
    """SID (chaîne « S-1-15-2-… ») du profil AppContainer d'APEX, créé au premier usage."""
    import ctypes
    from ctypes import wintypes

    w = _win32()
    sid = ctypes.c_void_p()
    hr = w.userenv.CreateAppContainerProfile(name, "APEX Sandbox", "Exécution isolée du code d'APEX", None, 0,
                                             ctypes.byref(sid))
    if (hr & 0xFFFFFFFF) == _HRESULT_ALREADY_EXISTS:
        hr = w.userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid))
    if hr != 0 or not sid.value:
        raise OSError(hr & 0xFFFFFFFF, f"profil AppContainer indisponible (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
    text = wintypes.LPWSTR()
    if not w.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _error("ConvertSidToStringSidW")
    try:
        return text.value or ""
    finally:
        w.k32.LocalFree(text)


def _grant(path: Path, sid: str, rights: str) -> None:
    """ACE héritable pour le SID de l'AppContainer (`icacls`, présent sur tout Windows). Lève si refusé."""
    # Chemin COMPLET d'icacls (jamais résolu par PATH : un icacls.exe planté dans le dossier courant ou le
    # PATH de l'utilisateur ne doit jamais recevoir le SID ni décider des droits).
    icacls = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "icacls.exe"
    out = subprocess.run([str(icacls), str(path), "/grant", f"*{sid}:(OI)(CI){rights}", "/Q"],  # noqa: S603
                         capture_output=True, text=True, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise OSError(out.returncode, f"icacls {path} : {(out.stderr or out.stdout).strip()[:300]}")


def _env_block(env: dict[str, str]) -> Any:
    import ctypes

    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    return ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in items) + "\0")


class _Run:
    """Un processus lancé dans l'AppContainer, sous Job Object — mesures réelles ou inconnues, jamais devinées."""

    exit_code: int | None = None
    timed_out = False
    cancelled = False
    peak_memory: int | None = None
    cpu_time_s: float | None = None
    stdout = b""
    stderr = b""
    stdout_cut = False
    stderr_cut = False


def _launch(argv: list[str], *, cwd: Path, env: dict[str, str], memory_mb: int, timeout_s: float,
            cancel_token: CancellationToken | None = None) -> _Run:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    w = _win32()
    sid = ctypes.c_void_p()
    hr = w.userenv.DeriveAppContainerSidFromAppContainerName(PROFILE_NAME, ctypes.byref(sid))
    if hr != 0:
        raise OSError(hr & 0xFFFFFFFF, "SID AppContainer introuvable")
    caps = w.SECURITY_CAPABILITIES(sid.value, None, 0, 0)

    sa = w.SECURITY_ATTRIBUTES(ctypes.sizeof(w.SECURITY_ATTRIBUTES), None, True)
    handles: dict[str, Any] = {}
    for name in ("in", "out", "err"):
        read, write = wintypes.HANDLE(), wintypes.HANDLE()
        if not w.k32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(sa), 0):
            raise _error("CreatePipe")
        handles[name] = (read, write)
    # Côté parent : jamais hérité ; côté enfant : stdin en lecture, stdout/stderr en écriture.
    w.k32.SetHandleInformation(handles["in"][1], _HANDLE_FLAG_INHERIT, 0)
    w.k32.SetHandleInformation(handles["out"][0], _HANDLE_FLAG_INHERIT, 0)
    w.k32.SetHandleInformation(handles["err"][0], _HANDLE_FLAG_INHERIT, 0)
    child_handles = (wintypes.HANDLE * 3)(handles["in"][0], handles["out"][1], handles["err"][1])

    size = ctypes.c_size_t(0)
    w.k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    attr_buffer = ctypes.create_string_buffer(size.value)
    if not w.k32.InitializeProcThreadAttributeList(attr_buffer, 2, 0, ctypes.byref(size)):
        raise _error("InitializeProcThreadAttributeList")
    job = None
    pi = w.PROCESS_INFORMATION()
    run = _Run()
    try:
        if not w.k32.UpdateProcThreadAttribute(attr_buffer, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                               ctypes.byref(caps), ctypes.sizeof(caps), None, None):
            raise _error("UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
        if not w.k32.UpdateProcThreadAttribute(attr_buffer, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                               child_handles, ctypes.sizeof(child_handles), None, None):
            raise _error("UpdateProcThreadAttribute(HANDLE_LIST)")
        si = w.STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(w.STARTUPINFOEXW)
        si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = handles["in"][0]
        si.StartupInfo.hStdOutput = handles["out"][1]
        si.StartupInfo.hStdError = handles["err"][1]
        si.lpAttributeList = ctypes.cast(attr_buffer, ctypes.c_void_p)

        job = w.k32.CreateJobObjectW(None, None)
        if not job:
            raise _error("CreateJobObjectW")
        limits = w.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (_LIMIT_ACTIVE_PROCESS | _LIMIT_PROCESS_MEMORY
                                                   | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION | _LIMIT_KILL_ON_JOB_CLOSE)
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
        if not w.k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise _error("SetInformationJobObject(limites)")
        ui = w.JOBOBJECT_BASIC_UI_RESTRICTIONS(_UILIMIT_ALL)
        w.k32.SetInformationJobObject(job, _JOB_BASIC_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui))

        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT
        if not w.k32.CreateProcessW(argv[0], command_line, None, None, True, flags, _env_block(env), str(cwd),
                                    ctypes.byref(si), ctypes.byref(pi)):
            raise _error("CreateProcessW")
        if not w.k32.AssignProcessToJobObject(job, pi.hProcess):
            error = _error("AssignProcessToJobObject")
            w.k32.TerminateProcess(pi.hProcess, 1)  # jamais lancé hors du job : suspendu, puis tué
            w.k32.CloseHandle(pi.hThread)
            w.k32.CloseHandle(pi.hProcess)
            pi.hProcess = None
            raise error
        w.k32.ResumeThread(pi.hThread)
    except BaseException:
        for key in ("in", "out", "err"):  # côté parent, sur échec : rien ne doit fuir
            w.k32.CloseHandle(handles[key][1] if key == "in" else handles[key][0])
        if job:
            w.k32.CloseHandle(job)
        raise
    finally:
        w.k32.DeleteProcThreadAttributeList(attr_buffer)
        for key in ("in", "out", "err"):  # extrémités de l'enfant : le parent ne les garde jamais
            w.k32.CloseHandle(handles[key][0] if key == "in" else handles[key][1])

    w.k32.CloseHandle(handles["in"][1])  # stdin : fin de fichier immédiate
    streams: dict[str, tuple[bytes, bool]] = {}

    def drain(name: str, handle: Any) -> None:
        fd = msvcrt.open_osfhandle(int(handle.value), os.O_RDONLY)  # type: ignore[attr-defined]
        kept, cut = bytearray(), False
        with os.fdopen(fd, "rb", buffering=0) as stream:
            while chunk := stream.read(65536):
                room = _MAX_CAPTURE_BYTES - len(kept)
                if room > 0:
                    kept.extend(chunk[:room])
                cut = cut or len(chunk) > room
        streams[name] = (bytes(kept), cut)

    readers = [threading.Thread(target=drain, args=(n, handles[n][0]), daemon=True) for n in ("out", "err")]
    for reader in readers:
        reader.start()
    if cancel_token is not None:
        cancel_token._register(on_cancel=lambda: w.k32.TerminateJobObject(job, 1),
                               is_alive=lambda: w.k32.WaitForSingleObject(pi.hProcess, 0) == _WAIT_TIMEOUT)
    try:
        waited = w.k32.WaitForSingleObject(pi.hProcess, max(1, int(timeout_s * 1000)))
        if waited == _WAIT_TIMEOUT:
            run.timed_out = True
            w.k32.TerminateJobObject(job, 1)
            w.k32.WaitForSingleObject(pi.hProcess, 10_000)
        code = wintypes.DWORD()
        if w.k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code)):
            run.exit_code = int(code.value)
        limits = w.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        if w.k32.QueryInformationJobObject(job, _JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits),
                                           None):
            run.peak_memory = int(limits.PeakProcessMemoryUsed)
        accounting = w.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if w.k32.QueryInformationJobObject(job, _JOB_BASIC_ACCOUNTING, ctypes.byref(accounting),
                                           ctypes.sizeof(accounting), None):
            run.cpu_time_s = (accounting.TotalUserTime + accounting.TotalKernelTime) / 1e7
    finally:
        w.k32.CloseHandle(job)  # KILL_ON_JOB_CLOSE : rien ne survit à l'exécution
        w.k32.CloseHandle(pi.hThread)
        w.k32.CloseHandle(pi.hProcess)
        for reader in readers:
            reader.join(timeout=10)
    run.cancelled = bool(cancel_token and cancel_token.is_cancel_requested)
    run.stdout, run.stdout_cut = streams.get("out", (b"", False))
    run.stderr, run.stderr_cut = streams.get("err", (b"", False))
    return run


_PROBE = r'''
import json, os, socket, subprocess, sys
r = {}
def attempt(key, fn, ok, ko):
    try:
        fn(); r[key] = ok
    except Exception as e:
        r[key] = ko + ":" + e.__class__.__name__
attempt("net_public", lambda: socket.create_connection(("1.1.1.1", 443), timeout=4).close(), "open", "blocked")
attempt("net_loopback", lambda: socket.create_connection(("127.0.0.1", __PORT__), timeout=4).close(), "open", "blocked")
attempt("read_secret", lambda: open(__SECRET__, encoding="utf-8").read(), "readable", "denied")
attempt("write_outside", lambda: open(__OUTSIDE__, "w").write("x"), "allowed", "denied")
attempt("write_inside", lambda: open(os.path.join(os.getcwd(), "inside.txt"), "w").write("x"), "ok", "denied")
attempt("spawn", lambda: subprocess.run([sys.executable, "-c", "pass"], timeout=20), "allowed", "denied")
attempt("memory", lambda: bytearray(__ALLOC__), "unbounded", "bounded")
print("__MARK__" + json.dumps(r))
'''

#: Ce que la sonde DOIT constater pour que l'isolation soit déclarée disponible.
_REQUIRED = {"net_public": "blocked", "net_loopback": "blocked", "read_secret": "denied",
             "write_outside": "denied", "write_inside": "ok", "spawn": "denied", "memory": "bounded"}


def probe_script(*, port: int, secret: str, outside: str, alloc_bytes: int, mark: str) -> str:
    return (_PROBE.replace("__PORT__", str(port)).replace("__SECRET__", repr(secret))
            .replace("__OUTSIDE__", repr(outside)).replace("__ALLOC__", str(alloc_bytes)).replace("__MARK__", mark))


def missing_guarantees(observed: dict[str, Any]) -> dict[str, Any]:
    """Garanties NON constatées par la sonde (vide = toutes constatées). Une clé absente est une garantie
    non constatée — jamais présumée."""
    return {k: observed.get(k) for k, want in _REQUIRED.items() if not str(observed.get(k, "")).startswith(want)}


def _unavailable(detail: str, **facts: Any) -> SandboxCapabilityReport:
    return SandboxCapabilityReport(
        level=IsolationLevel.NONE, namespaces_available=False, cgroups_available=False,
        privilege_drop_available=False, network_isolation_available=False, docker_daemon_reachable=False,
        seccomp_available=False, detail=detail[:900], facts={"backend": "windows_appcontainer", **facts},
    )


class WindowsAppContainerSandbox(SandboxProvider):
    def __init__(self, *, python_executable: str | None = None, work_root: Path | None = None,
                 capability_report: SandboxCapabilityReport | None = None) -> None:
        self._python = Path(python_executable or sys.executable)
        self._work_root = work_root
        self._cached_report = capability_report
        self._sid: str | None = None
        self._lock = threading.Lock()

    # -- détection empirique ---------------------------------------------------------------------------------

    def _root(self) -> Path:
        if self._work_root is not None:
            return self._work_root
        from apex.standalone import paths

        return paths().data_root / "sandbox"

    def _prepare(self) -> str:
        """Profil AppContainer + droit de LECTURE/EXÉCUTION sur le Python d'APEX. L'ACE est héritable : posée
        une fois (marqueur dans le dossier des sandboxes), puis seulement revérifiée — par la sonde, qui échoue
        si l'interpréteur n'est plus lisible depuis l'AppContainer."""
        with self._lock:
            if self._sid is None:
                sid = appcontainer_sid()
                marker = self._root() / f"acl-{sid}.ok"
                if not marker.exists():
                    _grant(self._python.parent, sid, "RX")
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(str(self._python.parent), encoding="utf-8")
                self._sid = sid
            return self._sid

    def detect(self) -> SandboxCapabilityReport:
        if self._cached_report is None:
            self._cached_report = self._probe()
        return self._cached_report

    def _probe(self) -> SandboxCapabilityReport:
        if sys.platform != "win32":
            return _unavailable("AppContainer : hôte non Windows")
        try:
            sid = self._prepare()
        except Exception as exc:
            return _unavailable(f"AppContainer indisponible sur cet hôte : {exc.__class__.__name__}: {exc}")
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="probe-", dir=root))
        secret = root.parent / f"sandbox-canary-{uuid.uuid4().hex}.txt"
        outside = root.parent / f"sandbox-outside-{uuid.uuid4().hex}.txt"
        listener = socket.socket()
        try:
            secret.write_text(secrets.token_hex(16), encoding="utf-8")
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            mark = f"APEX_PROBE_{uuid.uuid4().hex}:"
            script = probe_script(port=port, secret=str(secret), outside=str(outside),
                                  alloc_bytes=_PROBE_MEMORY_MB * 2 * 1024 * 1024, mark=mark)
            _grant(work, sid, "F")
            run = _launch([str(self._python), "-I", "-c", script], cwd=work, env=self._env(work),
                          memory_mb=_PROBE_MEMORY_MB, timeout_s=_PROBE_TIMEOUT_S)
            text = run.stdout.decode("utf-8", "replace")
            line = next((ln for ln in text.splitlines() if ln.startswith(mark)), None)
            if line is None:
                return _unavailable("sonde AppContainer sans résultat (code "
                                    f"{run.exit_code}) : {run.stderr.decode('utf-8', 'replace')[-400:]}",
                                    exit_code=run.exit_code)
            observed = json.loads(line[len(mark):])
            missing = missing_guarantees(observed)
            if missing:
                return _unavailable(f"garanties AppContainer NON constatées : {missing}", observed=observed)
            return SandboxCapabilityReport(
                level=IsolationLevel.APPCONTAINER, namespaces_available=False, cgroups_available=False,
                privilege_drop_available=True, network_isolation_available=True, docker_daemon_reachable=False,
                seccomp_available=False,
                detail=("AppContainer + Job Object constatés : réseau (Internet et local) refusé, fichiers de "
                        "l'utilisateur illisibles, écriture limitée au dossier de travail, aucun sous-processus, "
                        f"mémoire plafonnée ({_PROBE_MEMORY_MB} Mo sous sonde)"),
                facts={"backend": "windows_appcontainer", "observed": observed, "appcontainer_sid": sid},
            )
        except Exception as exc:
            return _unavailable(f"sonde AppContainer en erreur : {exc.__class__.__name__}: {exc}")
        finally:
            listener.close()
            for path in (secret, outside):
                with contextlib.suppress(OSError):
                    path.unlink()
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _env(work: Path) -> dict[str, str]:
        env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "TEMP": str(work), "TMP": str(work),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        return env

    # -- exécution -------------------------------------------------------------------------------------------

    def execute(self, spec: SandboxSpec, *, cancel_token: CancellationToken | None = None) -> SandboxResult:
        report = self.detect()
        if not report.is_available:
            raise IsolationUnavailable(
                "exécution de code (WindowsAppContainerSandbox)",
                required="appcontainer (réseau coupé, fichiers de l'utilisateur inaccessibles, aucun "
                         "sous-processus, mémoire plafonnée — constatés par sonde)",
                available=report.level.value,
            )
        if spec.limits.network != "none":
            raise IsolationUnavailable("réseau en sandbox", required="network=none", available=spec.limits.network)
        argv = list(spec.command)
        if not argv or argv[0] not in _PYTHON_ALIASES:
            raise IsolationUnavailable(f"commande « {argv[0] if argv else ''} » en sandbox Windows",
                                       required="python (seul interpréteur embarqué)", available="windows_appcontainer")
        argv[0] = str(self._python)
        sid = self._prepare()
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        started = time.perf_counter()
        try:
            for relative, content in spec.input_files:
                target = work / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            _grant(work, sid, "F")
            env = {**self._env(work), **{k: v for k, v in spec.env.items() if k.upper() not in {"PATH", "TEMP", "TMP"}}}
            run = _launch(argv, cwd=work, env=env, memory_mb=spec.limits.memory_mb,
                          timeout_s=spec.limits.timeout_s, cancel_token=cancel_token)
        finally:
            elapsed = time.perf_counter() - started
            shutil.rmtree(work, ignore_errors=True)
        stderr = run.stderr.decode("utf-8", errors="replace")
        limit_bytes = spec.limits.memory_mb * 1024 * 1024
        if run.cancelled:
            status = ExecutionStatus.CANCELLED
        elif run.timed_out:
            status = ExecutionStatus.TIMEOUT
        elif run.exit_code == 0:
            status = ExecutionStatus.SUCCESS
        elif run.peak_memory is not None and run.peak_memory >= 0.95 * limit_bytes:
            status = ExecutionStatus.OOM_KILLED
        else:
            status = ExecutionStatus.FAILED
        return SandboxResult(
            mission_id=spec.mission_id, clone_id=spec.clone_id, status=status, isolation_level=report.level,
            exit_code=Measurement.real(run.exit_code) if run.exit_code is not None else Measurement.unknown(),
            signal=Measurement.unknown(),
            elapsed_s=Measurement.real(round(elapsed, 3)),
            cpu_time_s=Measurement.real(run.cpu_time_s) if run.cpu_time_s is not None else Measurement.unknown(),
            memory_peak_mb=(Measurement.real(round(run.peak_memory / 1024 / 1024, 1))
                            if run.peak_memory is not None else Measurement.unknown()),
            pids_peak=Measurement.unknown(),
            stdout=run.stdout.decode("utf-8", errors="replace"), stderr=stderr,
            stdout_truncated=run.stdout_cut, stderr_truncated=run.stderr_cut,
            facts={"backend": "windows_appcontainer", "memory_limit_mb": spec.limits.memory_mb},
        )
