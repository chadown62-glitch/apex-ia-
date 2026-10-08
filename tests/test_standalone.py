"""APEX AUTONOME (profil « bureau » de l'application Windows `APEX.exe`) : sans WSL, sans Docker, base embarquée.

Prouve, sur cette machine : le profil et ses chemins ; le schéma SQLite créé, mis à niveau (colonne ajoutée) et
reconnu « à jour » ; le Doctor et le Startup Gate conscients du profil — l'infrastructure serveur mesurée, jamais
exigée, jamais déclarée réussie, et le profil serveur INCHANGÉ ; l'état des composants ; l'arrêt propre par
l'API ; un vrai démarrage puis un vrai arrêt du lanceur (`python -m apex.standalone`), dans un sous-processus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import apex.standalone as standalone
from apex.gate.model import GateCheckResult, GateStatus
from apex.gate.report import EnvironmentReport, StartupRole, startup_role
from apex.util.probe import FakeProbe

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    values = standalone.environment(tmp_path / "home")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    standalone.paths().ensure()
    return values


# -- profil ----------------------------------------------------------------------------------------------------


def test_the_profile_is_explicit_and_keeps_everything_under_apex_home(tmp_path) -> None:
    assert not standalone.is_standalone({}) and standalone.is_standalone({"APEX_PROFILE": "standalone"})
    env = standalone.environment(tmp_path / "h")
    assert env["APEX_DATABASE_URL"] == "sqlite:///" + (tmp_path / "h" / "data" / "apex.db").resolve().as_posix()
    assert env["APEX_CONFIG"].endswith("config.standalone.yaml") and Path(env["APEX_CONFIG"]).is_file()
    for key in ("APEX_DATA_ROOT", "APEX_MODELS_DIR", "APEX_DISCOVERY_DIR"):
        assert Path(env[key]).is_relative_to(tmp_path / "h")
    assert env["APEX_UI_DIST"].endswith(str(Path("frontend") / "dist"))


def test_the_standalone_config_declares_no_infrastructure_service(profile_env) -> None:
    from apex.config.loader import load_config

    config = load_config()
    assert "postgres" not in config.services and "redis" not in config.services
    assert config.paths.data_root == profile_env["APEX_DATA_ROOT"]
    native = [e for e in config.endpoints if e.runtime == "transformers"]
    assert native and native[0].url == "file://" + profile_env["APEX_MODELS_DIR"]


# -- base embarquée --------------------------------------------------------------------------------------------


def test_sqlite_schema_is_created_then_recognised_up_to_date(profile_env) -> None:
    from apex.db import schema

    engine = schema.engine_from_environment()
    assert schema.schema_state(engine).status.value == "missing"
    schema.upgrade_to_head()
    state = schema.schema_state(engine)
    assert state.ok and state.current == schema.head_revision()


def test_an_older_sqlite_base_gains_the_missing_columns_without_losing_data(profile_env) -> None:
    from sqlalchemy import inspect, text

    from apex.db import schema

    engine = schema.engine_from_environment()
    schema.sync_sqlite_schema(engine)
    with engine.begin() as connection:  # base d'une version antérieure : colonne absente
        connection.execute(text('ALTER TABLE task_runs DROP COLUMN plan_policy'))
        connection.execute(text("UPDATE alembic_version SET version_num = 'e8a0c2d4f6b8'"))
    assert schema.schema_state(engine).status.value == "behind"
    added = schema.sync_sqlite_schema(engine)
    assert "task_runs.plan_policy" in added
    assert "plan_policy" in {c["name"] for c in inspect(engine).get_columns("task_runs")}
    assert schema.schema_state(engine).ok


def test_the_api_uses_the_embedded_database(profile_env) -> None:
    from apex.api.dependencies import get_session_factory

    factory = get_session_factory()
    assert factory.kw["bind"].dialect.name == "sqlite"


# -- Doctor ----------------------------------------------------------------------------------------------------


def test_doctor_marks_server_infrastructure_not_applicable_and_checks_the_embedded_base(profile_env) -> None:
    from apex.config.loader import load_config
    from apex.db import schema
    from apex.doctor.check import CheckContext, Severity
    from apex.doctor.checks.standalone import NOT_APPLICABLE, check_host_platform_standalone, profile_checks
    from apex.doctor.runner import ALL_CHECKS

    schema.upgrade_to_head()
    checks = dict(profile_checks(ALL_CHECKS))
    ctx = CheckContext(probe=FakeProbe(platform={"system": "Windows", "release": "10"}), config=load_config())
    for name in NOT_APPLICABLE:
        outcome = checks[name](ctx)
        assert outcome.severity is Severity.SKIP and "sans objet" in outcome.message  # jamais un PASS non mesuré
    assert checks["database.local"](ctx).severity is Severity.PASS
    assert check_host_platform_standalone(ctx).severity is Severity.PASS
    darwin = CheckContext(probe=FakeProbe(platform={"system": "Darwin"}), config=None)
    assert check_host_platform_standalone(darwin).severity is Severity.FAIL
    # Remèdes du profil : jamais `docker compose` ni `pip install -e` pour un utilisateur d'APEX.exe ; la
    # mesure (et donc la sévérité) reste celle du contrôle d'origine.
    ollama = checks["service.ollama"](ctx)
    assert ollama.severity is Severity.WARN and "docker" not in ollama.remediation.lower()
    models = checks["models.native"](ctx)
    assert models.severity is Severity.WARN and "Installer le modèle" in models.remediation


# -- Startup Gate ----------------------------------------------------------------------------------------------


def _gate_ctx(profile: str, **probe):  # type: ignore[no-untyped-def]
    from apex.gate.checks import GateContext

    return GateContext(probe=FakeProbe(**probe), config=None, profile=profile)


def test_gate_measures_server_infrastructure_without_requiring_it_in_standalone() -> None:
    from apex.gate.checks import gate_02_wsl2, gate_06_docker, gate_16_tools, gate_21_compose_memory

    windows = {"platform": {"system": "Windows", "release": "10", "machine": "AMD64", "python": "3.12.10"}}
    assert gate_02_wsl2(_gate_ctx("standalone", **windows)).status is GateStatus.PASS
    assert gate_02_wsl2(_gate_ctx("server", **windows)).status is GateStatus.FAIL  # profil serveur inchangé
    docker = gate_06_docker(_gate_ctx("standalone"))
    assert docker.status is GateStatus.DEGRADED and "non requis" in docker.message
    assert gate_06_docker(_gate_ctx("server")).status is GateStatus.FAIL
    assert gate_21_compose_memory(_gate_ctx("standalone")).status is GateStatus.UNKNOWN
    tools = gate_16_tools(_gate_ctx("standalone"))  # git absent : jamais dur en profil autonome
    assert tools.status is not GateStatus.FAIL


def test_startup_roles_change_only_in_standalone() -> None:
    for check in ("GATE-06", "GATE-14", "GATE-19", "GATE-21"):
        assert startup_role(check) is StartupRole.PRECONDITION
        assert startup_role(check, "standalone") is StartupRole.OPTIONAL_CAPABILITY
    assert startup_role("GATE-02", "standalone") is StartupRole.PRECONDITION
    assert startup_role("GATE-05", "standalone") is StartupRole.PRECONDITION

    def result(check_id: str, status: GateStatus) -> GateCheckResult:
        return GateCheckResult(check_id=check_id, title=check_id, status=status, message="m",
                               unknown_reason="not_applicable" if status is GateStatus.UNKNOWN else "")

    checks = (result("GATE-02", GateStatus.PASS), result("GATE-05", GateStatus.PASS),
              result("GATE-21", GateStatus.UNKNOWN))
    assert EnvironmentReport(checks=checks, probe_is_real=True, profile="standalone").startup_authorized
    assert not EnvironmentReport(checks=checks, probe_is_real=True).startup_authorized  # serveur : bloque
    failing = checks + (result("GATE-06", GateStatus.FAIL),)
    assert not EnvironmentReport(checks=failing, probe_is_real=True, profile="standalone").startup_authorized


# -- état des composants et arrêt ------------------------------------------------------------------------------


def test_component_status_has_no_redis_and_keeps_research_out_of_the_verdict(profile_env) -> None:
    from apex.api.routes.status import overall
    from apex.obs import component_checks

    assert "redis" not in component_checks.profile_checks()
    verdict = overall([{"component": "brain", "state": "offline"}, {"component": "database", "state": "ready"}])
    assert verdict["verdict"] == "ready"


def test_the_shutdown_route_stops_only_a_server_that_registered_itself(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api import lifecycle
    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token

    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {get_or_create_api_token()}"}
    assert client.post("/api/v1/system/shutdown").status_code == 401  # jeton exigé, comme toute l'API
    assert client.post("/api/v1/system/shutdown", headers=headers).status_code == 409
    calls = []
    lifecycle.register_shutdown(lambda: calls.append(True))
    try:
        assert client.post("/api/v1/system/shutdown", headers=headers).status_code == 202
    finally:
        lifecycle.register_shutdown(None)
    assert calls == [True]


def test_process_helpers_and_the_single_instance_lock(tmp_path) -> None:
    from apex.standalone.process import SingleInstanceLock, alive, image_path, same_executable, wait_exit

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert alive(child.pid) and same_executable(image_path(child.pid), sys.executable)
    finally:
        child.kill()
        child.wait()
    assert wait_exit(child.pid, 2.0) and not alive(child.pid)
    first, second = SingleInstanceLock(tmp_path / "l"), SingleInstanceLock(tmp_path / "l")
    assert first.acquire()
    probe = subprocess.run([sys.executable, "-c", (
        "import sys; from pathlib import Path; from apex.standalone.process import SingleInstanceLock;"
        f"sys.exit(0 if SingleInstanceLock(Path({str(tmp_path / 'l')!r})).acquire() else 3)")],
        cwd=ROOT, capture_output=True)
    assert probe.returncode == 3  # un autre processus ne l'obtient pas
    first.release()
    assert second.acquire()
    second.release()


def test_stop_with_nothing_registered_or_a_refused_start_touches_no_process(profile_env, capsys) -> None:
    from apex.discovery import discovery_path, write_refused
    from apex.standalone.launcher import stop

    assert stop() == 0 and "rien à arrêter" in capsys.readouterr().out
    write_refused("127.0.0.1", 8000, Path.cwd(), exit_code=5, pid=999_999)
    assert stop() == 0 and not discovery_path().exists()


# -- vrai démarrage puis vrai arrêt (sous-processus, vrai HTTP) -------------------------------------------------


def test_the_launcher_really_starts_serves_and_stops(tmp_path) -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("APEX_")}
    env.update({"APEX_HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)})
    server = subprocess.Popen([sys.executable, "-m", "apex.standalone", "serve", "--port", str(port), "--log"],
                              cwd=tmp_path, env=env)
    try:
        record_path = tmp_path / "home" / ".apex" / "runtime" / "backend.json"
        deadline = time.monotonic() + 240
        status = None
        while time.monotonic() < deadline and server.poll() is None:
            time.sleep(2)
            if record_path.exists() and json.loads(record_path.read_text()).get("phase") == "serving":
                out = subprocess.run([sys.executable, "-m", "apex.standalone", "status"], env=env, cwd=tmp_path,
                                     capture_output=True, text=True)
                if out.returncode == 0:
                    status = json.loads(out.stdout)
                    break
        assert status is not None, (tmp_path / "home" / ".apex" / "runtime" / "launch.log").read_text()[-3000:]
        components = {c["component"]: c["state"] for c in status["api"]["components"]}
        assert components["database"] == "ready" and "redis" not in components
        assert status["api"]["profile"] == "standalone" and status["api"]["not_applicable"] == ["redis"]
        assert {"ollama", "brain"} <= set(status["api"]["informational"])
        log = (tmp_path / "home" / ".apex" / "runtime" / "launch.log").read_text(encoding="utf-8")
        assert "démarrage d'APEX autonome" in log  # journal UTF-8, accents compris
        second = subprocess.run([sys.executable, "-m", "apex.standalone", "serve", "--port", str(port)],
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
        assert second.returncode == 8  # un seul démarrage à la fois
        stopped = subprocess.run([sys.executable, "-m", "apex.standalone", "stop"], env=env, cwd=tmp_path,
                                 capture_output=True, text=True, timeout=90)
        assert stopped.returncode == 0 and "proprement" in stopped.stdout, stopped.stdout + stopped.stderr
        assert server.wait(timeout=30) == 0 and not record_path.exists()
    finally:
        if server.poll() is None:
            server.kill()
