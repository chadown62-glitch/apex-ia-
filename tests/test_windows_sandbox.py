"""Sandbox native Windows d'APEX autonome (`apex.sandbox.windows_appcontainer`) — ce qui se vérifie hors Windows.

L'AppContainer et le Job Object n'existent que sous Windows : leur EFFET réel est constaté sur la machine de
l'utilisateur par la sonde, à chaque démarrage, jamais supposé. Ici : la sonde elle-même (vrai Python, vrai
réseau, vrais fichiers), la règle qui exige CHAQUE garantie, le refus d'exécuter sans isolation constatée, et
l'ordre de sélection sous Windows (doubles d'infrastructure, jamais présentés comme une isolation réelle).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from apex.errors import IsolationUnavailable
from apex.sandbox import windows_appcontainer as wac
from apex.sandbox.model import IsolationLevel, ResourceLimits, SandboxCapabilityReport, SandboxSpec

ALL_GOOD = {"net_public": "blocked:OSError", "net_loopback": "blocked:TimeoutError", "read_secret": "denied:PermissionError",
            "write_outside": "denied:PermissionError", "write_inside": "ok", "spawn": "denied:OSError",
            "memory": "bounded:MemoryError"}


def test_every_guarantee_is_required_and_an_absent_one_is_never_presumed() -> None:
    assert wac.missing_guarantees(ALL_GOOD) == {}
    for key in ALL_GOOD:
        broken = {**ALL_GOOD, key: "open" if key.startswith("net") else "allowed"}
        assert key in wac.missing_guarantees(broken)
        partial = {k: v for k, v in ALL_GOOD.items() if k != key}
        assert key in wac.missing_guarantees(partial)


def test_the_probe_is_real_python_that_reports_every_guarantee(tmp_path) -> None:
    """Exécutée ICI sans AppContainer : la sonde voit un processus NON isolé et le dit (écritures permises,
    sous-processus permis, lecture du secret possible) — la règle refuserait donc cette « isolation »."""
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        script = wac.probe_script(port=listener.getsockname()[1], secret=str(secret),
                                  outside=str(tmp_path / "outside.txt"), alloc_bytes=1024, mark="MARK:")
        out = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                             timeout=120)
    finally:
        listener.close()
    line = next(line for line in out.stdout.splitlines() if line.startswith("MARK:"))
    observed = json.loads(line[len("MARK:"):])
    assert set(observed) == set(wac._REQUIRED)
    assert observed["net_loopback"] == "open" and observed["read_secret"] == "readable"
    assert observed["write_outside"] == "allowed" and observed["spawn"] == "allowed"
    assert set(wac.missing_guarantees(observed)) >= {"net_loopback", "read_secret", "write_outside", "spawn"}


def test_off_windows_the_backend_is_unavailable_and_refuses_to_execute(tmp_path) -> None:
    sandbox = wac.WindowsAppContainerSandbox(work_root=tmp_path)
    report = sandbox.detect()
    assert not report.is_available and report.level is IsolationLevel.NONE
    spec = SandboxSpec(mission_id="m", clone_id="c", command=("python3", "-c", "print(1)"),
                       limits=ResourceLimits(cpu_cores=1, memory_mb=256, pids_limit=8, timeout_s=10, disk_mb=16,
                                             network="none"))
    with pytest.raises(IsolationUnavailable):
        sandbox.execute(spec)


def _report(available: bool, detail: str) -> SandboxCapabilityReport:
    return SandboxCapabilityReport(
        level=IsolationLevel.APPCONTAINER if available else IsolationLevel.NONE, namespaces_available=False,
        cgroups_available=False, privilege_drop_available=available, network_isolation_available=available,
        docker_daemon_reachable=False, seccomp_available=False, detail=detail)


def test_windows_selection_prefers_the_verified_appcontainer_then_docker_and_never_fakes(monkeypatch) -> None:
    from apex.sandbox import container, provider

    monkeypatch.setattr(wac.WindowsAppContainerSandbox, "_probe", lambda self: _report(True, "constaté"))
    chosen = provider._select_on_windows("python:3.12-slim")
    assert chosen.provider_name == "WindowsAppContainerSandbox" and chosen.report.is_available

    monkeypatch.setattr(wac.WindowsAppContainerSandbox, "_probe", lambda self: _report(False, "API absente"))
    monkeypatch.setattr(container, "detect_container_capabilities", lambda image: _report(False, "docker absent"))
    chosen = provider._select_on_windows("python:3.12-slim")
    assert chosen.provider_name == "WindowsAppContainerSandbox" and not chosen.report.is_available
    assert "refusée" in chosen.reason


def test_appcontainer_is_real_isolation_but_never_a_hardened_container() -> None:
    assert IsolationLevel.APPCONTAINER.is_real_isolation
    assert IsolationLevel.APPCONTAINER is not IsolationLevel.HARDENED
    source = Path(wac.__file__).read_text(encoding="utf-8")
    assert "ActiveProcessLimit = 1" in source and "_LIMIT_KILL_ON_JOB_CLOSE" in source  # limites posées
    assert "CapabilityCount" in source and "SECURITY_CAPABILITIES(sid.value, None, 0, 0)" in source  # aucune capacité
