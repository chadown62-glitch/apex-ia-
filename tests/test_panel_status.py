"""Backend du Panel `APEX.exe` : `GET /api/v1/status` et l'enregistrement de découverte.

Les checks réseau sont exercés contre de VRAIS serveurs locaux (socket TCP, HTTP) qui parlent le
protocole attendu ou le violent ; aucun check n'est remplacé par un double. Ce ne sont pas de vrais
Redis/Ollama : ce qui est testé est la classification des réponses, pas ces services.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from apex import discovery
from apex.obs import component_checks as cc
from tests.api_test_support import TEST_API_TOKEN, authed_client

STATES = {cc.READY, cc.DEGRADED, cc.OFFLINE, cc.ERROR, cc.UNKNOWN}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def tcp_server() -> Iterator[callable]:  # type: ignore[valid-type]
    """Serveur TCP réel qui répond `reply` (ou rien si None) à la première lecture."""
    servers: list[socket.socket] = []

    def start(reply: bytes | None) -> tuple[str, int]:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        servers.append(srv)

        def serve() -> None:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.recv(64)
                if reply is not None:
                    conn.sendall(reply)
                else:
                    time.sleep(cc.NETWORK_TIMEOUT_S + 1)

        threading.Thread(target=serve, daemon=True).start()
        return srv.getsockname()

    yield start
    for srv in servers:
        srv.close()


@pytest.mark.parametrize(
    ("reply", "state"),
    [
        (b"+PONG\r\n", cc.READY),
        (b"-NOAUTH Authentication required.\r\n", cc.DEGRADED),
        (b"HTTP/1.1 400\r\n", cc.ERROR),
    ],
)
def test_redis_is_classified_from_its_real_reply(tcp_server, reply: bytes, state: str) -> None:  # type: ignore[no-untyped-def]
    assert cc.check_redis(tcp_server(reply)).state == state


def test_redis_refused_is_offline_and_silent_is_an_error(tcp_server) -> None:  # type: ignore[no-untyped-def]
    assert cc.check_redis(("127.0.0.1", _free_port())).state == cc.OFFLINE
    started = time.monotonic()
    silent = cc.check_redis(tcp_server(None))
    assert silent.state == cc.ERROR and time.monotonic() - started < cc.NETWORK_TIMEOUT_S + 1


class _Handler(http.server.BaseHTTPRequestHandler):
    body: bytes = b""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def http_server() -> Iterator[callable]:  # type: ignore[valid-type]
    started: list[http.server.HTTPServer] = []

    def start(body: bytes) -> tuple[str, int]:
        handler = type("H", (_Handler,), {"body": body})
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        started.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.server_address  # type: ignore[return-value]

    yield start
    for srv in started:
        srv.shutdown()


@pytest.mark.parametrize(
    ("body", "state"),
    [
        (json.dumps({"models": [{"name": "m"}]}).encode(), cc.READY),
        (json.dumps({"models": []}).encode(), cc.DEGRADED),
        (b"<html>not ollama</html>", cc.ERROR),
        (json.dumps({"other": 1}).encode(), cc.ERROR),
    ],
)
def test_ollama_is_classified_from_a_real_http_reply(http_server, body: bytes, state: str) -> None:  # type: ignore[no-untyped-def]
    assert cc.check_ollama(http_server(body)).state == state


def test_ollama_refused_is_offline() -> None:
    assert cc.check_ollama(("127.0.0.1", _free_port())).state == cc.OFFLINE


def test_sandbox_readiness_follows_the_detected_isolation() -> None:
    class Report:
        def __init__(self, available: bool) -> None:
            self.is_available = available
            self.level = "namespaced" if available else "rlimit_only"
            self.detail = "détection"

    assert cc.check_sandbox(lambda: Report(True)).state == cc.READY
    assert cc.check_sandbox(lambda: Report(False)).state == cc.OFFLINE

    def boom() -> None:
        raise RuntimeError("détection impossible")

    assert cc.check_sandbox(boom).state == cc.ERROR


def test_brain_is_never_ready_without_an_attested_champion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(cc.BRAIN_WORKDIR_ENV, raising=False)
    assert cc.check_brain().state == cc.OFFLINE
    assert cc.check_brain(str(tmp_path / "absent")).state == cc.ERROR
    assert cc.check_brain(str(tmp_path)).state == cc.DEGRADED  # répertoire vide : aucun champion


def test_a_check_slower_than_the_budget_is_unknown_never_positive() -> None:
    def slow() -> cc.ComponentStatus:
        time.sleep(2)
        return cc.ComponentStatus("slow", cc.READY, "trop tard")

    started = time.monotonic()
    out = cc.collect(
        {"slow": slow, "fast": lambda: cc.ComponentStatus("fast", cc.READY, "ok")}, budget_s=0.3
    )
    assert {c.component: c.state for c in out} == {"slow": cc.UNKNOWN, "fast": cc.READY}
    assert time.monotonic() - started < 1.5


def test_status_requires_the_token_and_reports_versions_and_states(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.version import PANEL_PROTOCOL, __version__

    assert TestClient(create_app()).get("/api/v1/status").status_code == 401
    wrong = TestClient(create_app(), headers={"Authorization": "Bearer faux"}).get("/api/v1/status")
    assert wrong.status_code == 401 and TEST_API_TOKEN not in wrong.text

    monkeypatch.setattr(cc, "CHECKS", {"redis": lambda: cc.check_redis(("127.0.0.1", _free_port()))})
    body = authed_client().get("/api/v1/status").json()
    data = body["data"]
    assert body["status"] == "real" and data["service"] == "apex"
    assert data["versions"] == {"apex": __version__, "api": "v1", "panel_protocol": PANEL_PROTOCOL}
    states = {c["component"]: c["state"] for c in data["components"]}
    assert states == {"api": cc.READY, "redis": cc.OFFLINE}
    assert all(c["state"] in STATES for c in data["components"])


def test_discovery_record_has_no_secret_and_is_removed_only_by_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path))
    path = discovery.write_discovery("127.0.0.1", 8123, tmp_path / "repo")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["port"] == 8123 and data["pid"] == os.getpid() and data["panel_protocol"] >= 1
    assert data["token_file"] == ".apex/api-token" and TEST_API_TOKEN not in path.read_text(
        encoding="utf-8"
    )
    assert not list(tmp_path.glob("*.tmp"))  # écriture atomique : aucun temporaire laissé

    other = dict(data, pid=data["pid"] + 1)
    path.write_text(json.dumps(other), encoding="utf-8")
    assert not discovery.remove_discovery() and path.exists()  # l'enregistrement d'une autre instance
    path.write_text(json.dumps(data), encoding="utf-8")
    assert discovery.remove_discovery() and not path.exists()
    assert not discovery.remove_discovery()  # déjà retiré : rien à faire, aucune erreur


@pytest.mark.parametrize("crash", [False, True])
def test_serve_publishes_discovery_while_serving_and_removes_it_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash: bool
) -> None:
    """`apex serve` : enregistrement présent PENDANT l'écoute (le Panel voit « en démarrage » puis
    « prêt »), retiré à la sortie, y compris si le serveur lève."""
    import types

    import uvicorn

    from apex.cli.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path / "runtime"))
    report = types.SimpleNamespace(phase_2_authorized=True, write=lambda _path: None)
    monkeypatch.setattr("apex.gate.runner.run_gate", lambda **_k: report)
    seen: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        seen["record"] = json.loads(discovery.discovery_path().read_text(encoding="utf-8"))
        seen["kwargs"] = kwargs
        if crash:
            raise RuntimeError("le serveur s'arrête brutalement")

    monkeypatch.setattr(uvicorn, "run", fake_run)
    if crash:
        with pytest.raises(RuntimeError):
            main(["serve", "--port", "8765"])
    else:
        assert main(["serve", "--port", "8765"]) == 0
    record = seen["record"]
    assert (
        isinstance(record, dict)
        and record["port"] == 8765
        and record["project_root"] == str(tmp_path.resolve())
    )
    assert not discovery.discovery_path().exists()


def test_serving_keeps_the_start_time_of_its_own_starting_announcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path))
    assert discovery.main(["starting", "--pid", str(os.getpid()), "--port", "8000"]) == 0
    started = json.loads(discovery.discovery_path().read_text(encoding="utf-8"))
    assert started["phase"] == "starting"

    discovery.write_discovery("127.0.0.1", 8000, tmp_path, phase="serving")
    serving = json.loads(discovery.discovery_path().read_text(encoding="utf-8"))
    assert serving["phase"] == "serving" and serving["started_at"] == started["started_at"]

    # Un autre pid ne réutilise jamais l'heure d'un démarrage qui n'est pas le sien.
    discovery.main(["starting", "--pid", "1", "--port", "8000"])
    discovery.write_discovery("127.0.0.1", 8000, tmp_path, phase="serving")
    assert json.loads(discovery.discovery_path().read_text(encoding="utf-8"))["pid"] == os.getpid()
    with pytest.raises(ValueError):
        discovery.record("127.0.0.1", 8000, tmp_path, phase="autre")


def test_a_gate_refusal_withdraws_the_starting_announcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    from apex.cli.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(discovery.DISCOVERY_DIR_ENV, str(tmp_path / "runtime"))
    discovery.write_discovery(
        "127.0.0.1", 8000, tmp_path, phase="starting"
    )  # annonce de launch.sh (même pid)
    refused = types.SimpleNamespace(phase_2_authorized=False, write=lambda _path: None)
    monkeypatch.setattr("apex.gate.runner.run_gate", lambda **_k: refused)

    assert main(["serve"]) == 1
    assert not discovery.discovery_path().exists()


_SERVER = """
import sys
from pathlib import Path

import uvicorn

from apex.discovery import cleanup_on_shutdown, write_discovery

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

port = int(sys.argv[1])
write_discovery("127.0.0.1", port, Path("."))
served = cleanup_on_shutdown(app) if sys.argv[2] == "wrapped" else app
uvicorn.run(served, host="127.0.0.1", port=port, log_level="warning")
"""


@pytest.mark.parametrize(("sig", "wrapped"), [("SIGTERM", True), ("SIGINT", True), ("SIGTERM", False)])
def test_a_signal_stopped_server_leaves_no_record_only_with_the_shutdown_hook(
    tmp_path: Path, sig: str, wrapped: bool
) -> None:
    """Vrai processus uvicorn, vrai signal. Sans l'enveloppe, l'enregistrement RESTE (uvicorn relance
    le signal après l'arrêt : le `finally` ne s'exécute jamais) — c'est le défaut constaté en E2E."""
    import signal
    import subprocess
    import sys

    port = _free_port()
    env = dict(os.environ, **{discovery.DISCOVERY_DIR_ENV: str(tmp_path)})
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(port), "wrapped" if wrapped else "plain"], cwd=repo, env=env
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        record = tmp_path / "backend.json"
        assert json.loads(record.read_text(encoding="utf-8"))["pid"] == proc.pid
        proc.send_signal(getattr(signal, sig))
        proc.wait(timeout=30)
    finally:
        proc.kill()
    assert record.exists() is (not wrapped)
