"""Démarrer / arrêter APEX sans terminal : ce que le bouton du Panel exécute dans WSL.

Intégration réelle : le VRAI `scripts/apex-launch.sh` (copié), le VRAI interpréteur et le VRAI
`python -m apex.discovery` ; seuls `docker` et le serveur APEX sont des doubles (pas de Docker ni de
base dans ce test) — le double du serveur est un vrai processus dont la ligne de commande contient
`apex.cli.main serve`, comme `apex serve` après `exec`.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from apex import discovery
from tests.test_launcher import LauncherFixture

REPO = Path(__file__).resolve().parent.parent


def _fixture(tmp_path: Path, compose_up_exit: int = 1) -> LauncherFixture:
    fx = LauncherFixture(tmp_path)
    fx.write_env("FOO=bar\n")
    venv_bin = fx.root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(sys.executable)
    (fx.root / "apex").symlink_to(REPO / "apex")
    fx.write_fake_docker(compose_up_exit=compose_up_exit, bind_declared_ports=False)
    return fx


def _run(fx: LauncherFixture, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(fx.launcher), *args],
        cwd=str(fx.root),
        env={"PATH": str(fx.bin_dir), "HOME": str(fx.root)},
        capture_output=True,
        text=True,
        timeout=90,
    )


def _runtime(fx: LauncherFixture) -> Path:
    return fx.root / ".apex" / "runtime"


# -- panel-serve : même préflight que serve, sortie dans un journal ---------------------------------

def test_panel_serve_writes_its_output_to_the_launch_log_and_rotates_the_previous_one(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    runtime = _runtime(fx)
    runtime.mkdir(parents=True)
    (runtime / "launch.log").write_text("ancien démarrage\n", encoding="utf-8")

    result = _run(fx, "panel-serve", "--port", "8123")

    assert result.returncode == 2  # compose up en échec (double) : même code que `serve`
    assert result.stdout == "" and result.stderr == ""  # rien vers le Panel : tout est dans le journal
    log = (runtime / "launch.log").read_text(encoding="utf-8")
    assert "démarrage demandé depuis le Panel APEX" in log
    assert "racine du projet" in log and "docker compose" in log
    assert (runtime / "launch.log.1").read_text(encoding="utf-8") == "ancien démarrage\n"
    record = json.loads((runtime / "backend.json").read_text(encoding="utf-8"))
    assert record["phase"] == "refused" and record["port"] == 8123
    assert record["refusal"]["exit_code"] == 2 and record["refusal"]["stage"] == "infrastructure"


def test_panel_serve_runs_exactly_the_serve_preflight(tmp_path: Path) -> None:
    """Aucune étape sautée : .env absent -> 1, comme `serve` (le Startup Gate est dans ce même chemin)."""
    fx = _fixture(tmp_path)
    (fx.root / ".env").unlink()

    result = _run(fx, "panel-serve")

    assert result.returncode == 1
    assert ".env introuvable" in (_runtime(fx) / "launch.log").read_text(encoding="utf-8")


# -- un seul démarrage à la fois ---------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["serve", "panel-serve"])
def test_a_second_launch_is_refused_while_apex_holds_the_lock(tmp_path: Path, mode: str) -> None:
    fx = _fixture(tmp_path)
    runtime = _runtime(fx)
    runtime.mkdir(parents=True)
    (runtime / "launch.log").write_text("journal du backend en marche\n", encoding="utf-8")
    discovery_before = {"phase": "serving", "pid": 1}
    (runtime / "backend.json").write_text(json.dumps(discovery_before), encoding="utf-8")

    with open(runtime / "launch.lock", "a") as lock:  # noqa: PTH123 — le backend en marche tient ce verrou
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(fx, mode)

    assert result.returncode == 8
    if mode == "serve":
        assert "déjà en cours de démarrage ou d'exécution" in result.stderr
    # Rien touché : ni le journal du backend en marche, ni son enregistrement, ni Docker.
    assert (runtime / "launch.log").read_text(encoding="utf-8") == "journal du backend en marche\n"
    assert not (runtime / "launch.log.1").exists()
    assert json.loads((runtime / "backend.json").read_text(encoding="utf-8")) == discovery_before
    assert not fx.docker_calls_log.exists()


def test_the_lock_is_released_when_the_launch_ends(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)

    first = _run(fx, "serve")
    second = _run(fx, "serve")

    assert (first.returncode, second.returncode) == (2, 2)  # jamais 8 : le verrou meurt avec le processus


# -- stop : arrête le backend enregistré, vérifié comme APEX ----------------------------------------

_FAKE_SERVER = """
import signal, sys, time
ignore_int = sys.argv[1] == "ignore-int"
if ignore_int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
open(sys.argv[2], "w").write("ready")
while True:
    time.sleep(0.1)
"""


def _start_fake_backend(fx: LauncherFixture, tmp_path: Path, *, marker: str, ignore_int: bool = False,
                        ) -> subprocess.Popen[bytes]:
    ready = tmp_path / f"ready-{marker}"
    proc = subprocess.Popen(
        [sys.executable, "-c", _FAKE_SERVER, "ignore-int" if ignore_int else "normal", str(ready), marker, "serve"]
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.environ[discovery.DISCOVERY_DIR_ENV] = str(_runtime(fx))
    try:
        discovery.write_discovery("127.0.0.1", 8000, fx.root, phase="serving", pid=proc.pid)
    finally:
        del os.environ[discovery.DISCOVERY_DIR_ENV]
    return proc


def _gone(proc: subprocess.Popen[bytes]) -> bool:
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return False
    return True


def test_stop_stops_the_registered_apex_backend(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    backend = _start_fake_backend(fx, tmp_path, marker="apex.cli.main")
    try:
        result = _run(fx, "stop")
        assert result.returncode == 0, result.stderr
        assert _gone(backend) and backend.returncode == -signal.SIGINT  # arrêt propre demandé d'abord
        assert "backend APEX arrêté" in result.stderr
        assert not (_runtime(fx) / "backend.json").exists()
    finally:
        backend.kill()


def test_stop_escalates_when_the_backend_ignores_the_polite_signal(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    backend = _start_fake_backend(fx, tmp_path, marker="apex.cli.main", ignore_int=True)
    try:
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "apex.discovery", "stop", "--timeout", "1"],
            cwd=str(REPO), env={**os.environ, discovery.DISCOVERY_DIR_ENV: str(_runtime(fx))},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert _gone(backend) and backend.returncode == -signal.SIGTERM
        assert "envoi de SIGTERM" in result.stderr and time.monotonic() - started < 30
    finally:
        backend.kill()


def test_stop_never_signals_a_pid_that_is_no_longer_apex(tmp_path: Path) -> None:
    """Pid réutilisé par un autre programme (arrêt brutal précédent) : enregistrement effacé, aucun signal."""
    fx = _fixture(tmp_path)
    other = _start_fake_backend(fx, tmp_path, marker="un.autre.programme")
    try:
        result = _run(fx, "stop")
        assert result.returncode == 0
        assert "n'est plus APEX" in result.stderr
        assert other.poll() is None  # toujours vivant
        assert not (_runtime(fx) / "backend.json").exists()
    finally:
        other.kill()


def test_stop_clears_a_refusal_and_does_nothing_else(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    _run(fx, "serve")  # compose en échec -> enregistrement « refused »
    assert json.loads((_runtime(fx) / "backend.json").read_text())["phase"] == "refused"

    result = _run(fx, "stop")

    assert result.returncode == 0
    assert not (_runtime(fx) / "backend.json").exists()


def test_stop_without_any_backend_is_a_noop(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)

    result = _run(fx, "stop")

    assert result.returncode == 0 and "rien à arrêter" in result.stderr


# -- raison du refus : seulement le rapport du Gate de CE démarrage ---------------------------------

def _report(path: Path, generated_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": generated_at,
        "startup": {"authorized": False, "blocking": ["GATE-21"],
                    "unverified_blocking": [{"check_id": "GATE-03", "status": "unknown", "reason": "x"}]},
    }), encoding="utf-8")


def test_refusal_cites_the_gate_checks_of_this_launch(tmp_path: Path) -> None:
    report = tmp_path / ".apex" / "environment.json"
    _report(report, "2026-10-02T19:00:05Z")

    assert discovery.gate_ids_since(report, "2026-10-02T19:00:04.900000+00:00") == (["GATE-21"], ["GATE-03"])


def test_an_older_gate_report_is_never_cited(tmp_path: Path) -> None:
    report = tmp_path / ".apex" / "environment.json"
    _report(report, "2026-10-02T18:00:00Z")

    assert discovery.gate_ids_since(report, "2026-10-02T19:00:00+00:00") == ([], [])
    assert discovery.gate_ids_since(report, None) == ([], [])
    assert discovery.gate_ids_since(tmp_path / "absent.json", "2026-10-02T19:00:00+00:00") == ([], [])


def test_refused_cli_reads_the_report_of_this_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path / "runtime"))
    discovery.write_discovery("127.0.0.1", 8000, tmp_path, phase="starting", pid=4242)
    _report(tmp_path / ".apex" / "environment.json", "2999-01-01T00:00:00Z")

    assert discovery.main(["refused", "--pid", "4242", "--code", "5"]) == 0

    record = json.loads(discovery.discovery_path().read_text(encoding="utf-8"))
    assert record["phase"] == "refused" and record["pid"] == 4242
    assert record["refusal"] == {**record["refusal"], "exit_code": 5, "stage": "gate",
                                 "blocking": ["GATE-21"], "unverified_blocking": ["GATE-03"]}


def test_refusal_record_never_carries_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path / "runtime"))
    path = discovery.write_refused("127.0.0.1", 8000, tmp_path, exit_code=4, pid=7)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {"schema", "service", "phase", "apex_version", "api_version", "panel_protocol",
                           "project_root", "token_file", "host", "port", "pid", "started_at", "refusal"}
    assert set(record["refusal"]) == {"exit_code", "stage", "blocking", "unverified_blocking", "at"}
    assert record["refusal"]["stage"] == "doctor"
