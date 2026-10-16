"""Accélération GPU facultative d'APEX autonome (`apex.standalone.gpu`) : variante choisie d'après le
pilote, installation à part avec sonde, activation au démarrage avec repli sur le processeur, retrait ;
générateur des listes figées (`setup/packaging/runtime/gpu/lock_gpu.py`).

Le pilote, pip et la sonde sont des doubles (`_Machine`) : ces tests prouvent la logique de décision et de
repli, PAS qu'un GPU fonctionne — aucune compatibilité GPU n'est établie ici (cette machine n'a pas de GPU).
La vraie sonde est exécutée contre le vrai torch de cet environnement : elle mesure, elle ne suppose pas.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import io
import json
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

import apex.standalone as standalone
from apex.standalone import gpu

ROOT = Path(__file__).resolve().parents[1]
BASE_LOCK = ROOT / "setup/packaging/runtime/requirements-windows.lock"
GPU_DIR = ROOT / "setup/packaging/runtime/gpu"
SMI = "/pilote/nvidia-smi"
#: Format de l'en-tête de `nvidia-smi` ; valeurs inventées pour le test (aucune mesure réelle).
HEADER = """+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 999.01                 Driver Version: 999.01         CUDA Version: {cuda}     |
|-----------------------------------------+------------------------+----------------------+
"""
QUERY = "GPU de test, 999.01, 8188, 8.9\n"


def _ok_probe(**overrides):
    return {"torch": "2.14.0+cu128", "from_overlay": True, "torch_cuda": "12.8", "cuda_available": True,
            "devices": [{"index": 0, "name": "GPU de test", "capability": "8.9", "total_memory_mib": 8188}],
            "matmul": {"ok": True, "max_abs_err": 1e-5, "ms": 1.0}, **overrides}


class _Machine:
    """Double du PC : `nvidia-smi`, pip (qui « installe » un paquet torch vide) et la sonde."""

    def __init__(self, *, cuda="12.8", query=QUERY, probe=None, pip_code=0, header_code=0):
        self.cuda, self.query, self.pip_code, self.header_code = cuda, query, pip_code, header_code
        self.probe_result = _ok_probe() if probe is None else probe
        self.calls: list[list[str]] = []
        self.pip_env: dict[str, str] | None = None

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[0] == SMI:
            if len(cmd) == 1:
                text = HEADER.format(cuda=self.cuda) if self.cuda else "NVIDIA-SMI sans version CUDA\n"
                return subprocess.CompletedProcess(cmd, self.header_code, text, "erreur du pilote")
            code = 0 if self.query is not None else 6
            return subprocess.CompletedProcess(cmd, code, self.query or "", "")
        if cmd[1:4] == ["-m", "pip", "install"]:
            self.pip_env = kwargs.get("env")
            if self.pip_code == 0:
                target = Path(cmd[cmd.index("--target") + 1])
                (target / "torch").mkdir(parents=True)
                (target / "torch" / "__init__.py").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, self.pip_code, "", "")
        if cmd[1:3] == ["-I", "-c"]:
            out = json.dumps({**self.probe_result, "overlay": cmd[4]})
            return subprocess.CompletedProcess(cmd, 0, out + "\n", "")
        raise AssertionError(f"commande inattendue : {cmd}")

    def ran(self, what: str) -> bool:
        return any(what in " ".join(c) for c in self.calls)


@pytest.fixture
def app(tmp_path):
    """Une application installée dont les trois variantes sont figées (listes et tailles)."""
    folder = tmp_path / "app" / "runtime" / "gpu"
    folder.mkdir(parents=True)
    spec = json.loads((ROOT / "setup/packaging/runtime/gpu/gpu-runtime.json").read_text(encoding="utf-8"))
    for variant in spec["variants"]:
        variant.update(wheel_bytes=2_000_000_000, installed_bytes=4_000_000_000)
        line = f"torch @ https://exemple/{variant['id']}.whl --hash=sha256:{'0' * 64}\n"
        (folder / variant["lock"]).write_text(line, encoding="utf-8")
    (folder / "gpu-runtime.json").write_text(json.dumps(spec), encoding="utf-8")
    return tmp_path / "app"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def _install(home, app, machine, **kwargs):
    kwargs.setdefault("disk_free", lambda _path: 10**12)
    kwargs.setdefault("running", lambda: False)
    return gpu.install(home, root=app, run=machine, smi=SMI, python="python-apex", **kwargs)


def _activate(home, app, machine, path):
    return gpu.activate(home, root=app, run=machine, smi=SMI, sys_path=path)


@pytest.fixture
def no_torch(monkeypatch):
    """L'activation se fait avant tout import de torch : ce processus de test, lui, l'a peut-être chargé."""
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.delenv(gpu.OVERLAY_ENV, raising=False)


# -- description livrée -----------------------------------------------------------------------------------


def test_the_shipped_description_offers_no_variant_until_its_locks_are_generated() -> None:
    spec, folder, problem = gpu.load_spec(ROOT)
    assert problem is None and folder == ROOT / "setup/packaging/runtime/gpu"
    assert [v["id"] for v in spec["variants"]] == ["cu130", "cu128", "cu126"]
    lock = (ROOT / "setup/packaging/runtime/requirements-windows.lock").read_text(encoding="utf-8")
    assert f"torch=={spec['torch']} " in lock  # même torch que le runtime de base
    if not any(v["prepared"] for v in gpu.variants(spec, folder)):
        driver = {"found": True, "cuda_driver": "12.8", "gpus": [{"name": "x"}]}
        variant, why = gpu.choose(spec, folder, driver)
        assert variant is None and "non préparé" in why and "lock_gpu.py" in why


def test_the_release_build_ships_only_the_description_and_its_locks() -> None:
    script = (ROOT / "setup/packaging/build_desktop_release.sh").read_text(encoding="utf-8")
    assert 'cp "$SCRIPT_DIR/runtime/gpu/gpu-runtime.json" "$APP_DIR/runtime/gpu/"' in script
    assert "torch-cu*.lock" in script and ".whl" not in script


# -- pilote ---------------------------------------------------------------------------------------------


def test_the_driver_is_read_from_nvidia_smi_never_guessed(monkeypatch) -> None:
    driver = gpu.detect_driver(smi=SMI, run=_Machine(cuda="12.8"))
    assert driver["found"] and driver["cuda_driver"] == "12.8" and driver["driver_version"] == "999.01"
    assert driver["gpus"] == [{"name": "GPU de test", "driver_version": "999.01", "memory_total_mib": 8188,
                               "compute_capability": "8.9"}]
    assert "reason" not in driver
    monkeypatch.setattr(gpu.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    assert gpu.detect_driver(run=_Machine()) == {"found": False, "reason": (
        "aucun pilote NVIDIA détecté (nvidia-smi introuvable)")}
    failing = gpu.detect_driver(smi=SMI, run=_Machine(header_code=9))
    assert failing["found"] is False and "nvidia-smi a échoué" in failing["reason"]
    unreadable = gpu.detect_driver(smi=SMI, run=_Machine(cuda=None))
    assert "version CUDA du pilote illisible" in unreadable["reason"]
    assert "aucun GPU" in gpu.detect_driver(smi=SMI, run=_Machine(query=""))["reason"]


@pytest.mark.parametrize(("cuda", "expected"), [("13.0", "cu130"), ("13.1", "cu130"), ("12.9", "cu128"),
                                                ("12.8", "cu128"), ("12.6", "cu126"), ("12.4", None)])
def test_the_variant_never_exceeds_what_the_driver_accepts(app, cuda, expected) -> None:
    spec, folder, _ = gpu.load_spec(app)
    variant, why = gpu.choose(spec, folder, gpu.detect_driver(smi=SMI, run=_Machine(cuda=cuda)))
    assert (variant["id"] if variant else None) == expected
    if expected is None:
        assert "pilote NVIDIA trop ancien" in why and "mettez à jour" in why


# -- installation ---------------------------------------------------------------------------------------


def test_install_puts_torch_cuda_aside_and_keeps_it_only_after_a_successful_probe(home, app) -> None:
    machine = _Machine(cuda="12.8")
    code, report = _install(home, app, machine)
    assert code == gpu.EXIT_OK and report["ok"] and report["variant"] == "cu128"
    overlay = gpu.gpu_home(home) / "torch-2.14.0-cu128"
    assert (overlay / "torch" / "__init__.py").is_file()
    assert not list((gpu.gpu_home(home)).glob("*.partial")) and not (gpu.gpu_home(home) / ".tmp").exists()
    [pip] = [c for c in machine.calls if c[1:4] == ["-m", "pip", "install"]]
    for flag in ("--require-hashes", "--no-deps", "--only-binary=:all:", "--no-cache-dir"):
        assert flag in pip
    assert pip[pip.index("-r") + 1].endswith("torch-cu128.lock")
    assert machine.pip_env["TMP"].startswith(str(gpu.gpu_home(home)))  # tout sur le disque d'APEX_HOME
    marker = json.loads((gpu.gpu_home(home) / "installed.json").read_text(encoding="utf-8"))
    assert marker["overlay"] == str(overlay) and marker["variant"] == "cu128"
    lock = (app / "runtime/gpu/torch-cu128.lock").read_bytes()
    assert marker["lock_sha256"] == hashlib.sha256(lock).hexdigest()
    assert marker["probe"]["from_overlay"] is True and gpu.enabled(home)


@pytest.mark.parametrize("probe", [
    _ok_probe(cuda_available=False),
    _ok_probe(from_overlay=False, torch_file="/base/torch/__init__.py"),
    _ok_probe(torch_cuda=None),
    _ok_probe(matmul={"ok": False, "error": "no kernel image is available for execution on the device"}),
    {"error": "import torch : OSError: [WinError 126] module introuvable"},
])
def test_a_failed_probe_leaves_nothing_installed_and_says_why(home, app, probe) -> None:
    code, report = _install(home, app, _Machine(probe=probe))
    assert code == gpu.EXIT_PROBE_FAILED and report["ok"] is False
    assert "sonde GPU en échec, surcouche retirée" in report["reason"]
    assert not gpu.marker_path(home).exists() and not list(gpu.gpu_home(home).glob("torch-*"))
    last = json.loads((gpu.gpu_home(home) / "last-install.json").read_text(encoding="utf-8"))
    assert last["reason"] == report["reason"]


@pytest.mark.parametrize(("machine", "kwargs", "reason"), [
    (_Machine(), {"running": lambda: True}, "APEX est en cours d'exécution"),
    (_Machine(header_code=9), {}, "nvidia-smi a échoué"),
    (_Machine(cuda="11.8"), {}, "pilote NVIDIA trop ancien"),
    (_Machine(), {"disk_free": lambda _path: 5 * 10**9}, "espace disque insuffisant"),
    (_Machine(), {"requested": "cu999"}, "variante inconnue"),
])
def test_install_refuses_before_downloading_anything(home, app, machine, kwargs, reason) -> None:
    code, report = _install(home, app, machine, **kwargs)
    assert code == gpu.EXIT_REFUSED and reason in report["reason"]
    assert not machine.ran("pip") and not (gpu.gpu_home(home) / "installed.json").exists()


def test_a_pip_failure_leaves_nothing_behind(home, app) -> None:
    code, report = _install(home, app, _Machine(pip_code=1))
    assert code == gpu.EXIT_INSTALL_FAILED and "pip a échoué (code 1)" in report["reason"]
    assert not list(gpu.gpu_home(home).glob("torch-*")) and not gpu.marker_path(home).exists()


def test_a_reinstall_replaces_the_previous_overlay(home, app) -> None:
    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK
    stale = gpu.gpu_home(home) / "torch-2.14.0-cu128" / "ancien.txt"
    stale.write_text("x", encoding="utf-8")
    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK
    assert not stale.exists() and not list((gpu.gpu_home(home)).glob("*.old"))


# -- activation au démarrage ------------------------------------------------------------------------------


def test_without_the_component_nothing_is_measured_and_apex_runs_on_the_cpu(home, app, no_torch) -> None:
    machine, path = _Machine(), ["base"]
    result = _activate(home, app, machine, path)
    assert result["state"] == "absent" and path == ["base"] and machine.calls == []  # pas même nvidia-smi
    assert gpu.last_activation(home)["state"] == "absent"


def test_activation_puts_the_overlay_first_only_when_everything_is_verified(home, app, no_torch) -> None:
    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK
    machine, path = _Machine(), ["base"]
    result = _activate(home, app, machine, path)
    overlay = str(gpu.gpu_home(home) / "torch-2.14.0-cu128")
    assert result["state"] == "active" and path == [overlay, "base"]
    assert machine.ran(SMI) and machine.ran("-I -c")  # pilote ET sonde, à chaque démarrage
    assert gpu.os.environ[gpu.OVERLAY_ENV] == overlay
    assert gpu.last_activation(home)["state"] == "active"


@pytest.mark.parametrize(("change", "state", "reason"), [
    ("disable", "disabled", "désactivée par l'utilisateur"),
    ("update", "inactive", "APEX a été mis à jour"),
    ("downgrade", "inactive", "pilote NVIDIA insuffisant"),
    ("no_driver", "inactive", "pilote NVIDIA indisponible"),
    ("probe", "inactive", "sonde GPU en échec"),
    ("deleted", "failed", "surcouche GPU incomplète ou effacée"),
    ("torch_loaded", "failed", "torch déjà chargé"),
])
def test_activation_falls_back_to_the_cpu_and_says_why(home, app, no_torch, monkeypatch, change, state,
                                                       reason) -> None:
    import shutil

    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK
    machine = _Machine()
    if change == "disable":
        gpu.set_enabled(False, home)
    elif change == "update":
        lock = app / "runtime/gpu/torch-cu128.lock"
        lock.write_text("torch @ autre --hash=sha256:1\n", encoding="utf-8")
    elif change == "downgrade":
        machine = _Machine(cuda="12.6")
    elif change == "no_driver":
        machine = _Machine(header_code=9)
    elif change == "probe":
        machine = _Machine(probe=_ok_probe(cuda_available=False))
    elif change == "deleted":
        shutil.rmtree(gpu.gpu_home(home) / "torch-2.14.0-cu128")
    else:
        monkeypatch.setitem(sys.modules, "torch", object())
    path = ["base"]
    result = _activate(home, app, machine, path)
    assert result["state"] == state and reason in result["reason"], result
    assert path == ["base"] and gpu.OVERLAY_ENV not in gpu.os.environ
    assert gpu.last_activation(home)["state"] == state


def test_activation_never_raises(home, app, no_torch, monkeypatch) -> None:
    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK

    def broken(_root=None):
        raise RuntimeError("description corrompue")

    monkeypatch.setattr(gpu, "load_spec", broken)
    path = ["base"]
    result = _activate(home, app, _Machine(), path)
    assert result["state"] == "failed" and "description corrompue" in result["reason"] and path == ["base"]


def test_remove_restores_the_cpu_only_state_and_never_while_apex_runs(home, app, no_torch) -> None:
    assert _install(home, app, _Machine())[0] == gpu.EXIT_OK
    code, report = gpu.remove(home, running=lambda: True)
    assert code == gpu.EXIT_REFUSED and (gpu.gpu_home(home) / "installed.json").exists()
    code, report = gpu.remove(home, running=lambda: False)
    assert code == gpu.EXIT_OK and report["removed"] == ["torch-2.14.0-cu128"]
    assert not (gpu.gpu_home(home) / "installed.json").exists()
    assert _activate(home, app, _Machine(), [])["state"] == "absent"


# -- la vraie sonde ---------------------------------------------------------------------------------------


def _overlay(tmp_path, name, init):
    folder = tmp_path / name
    (folder / "torch").mkdir(parents=True)
    (folder / "torch" / "__init__.py").write_text(init, encoding="utf-8")
    return folder


def test_the_real_probe_runs_apart_and_measures_instead_of_assuming(tmp_path) -> None:
    pytest.importorskip("torch")
    # Surcouche vide : c'est le torch de BASE qui serait chargé — refusé, quel que soit ce qu'il sait faire.
    empty = tmp_path / "vide"
    empty.mkdir()
    measured = gpu.probe(empty)
    assert measured["from_overlay"] is False and "hors de la surcouche" in gpu.probe_verdict(measured)
    # Surcouche dont l'import échoue (DLL absente) : dit tel quel, sans atteindre ce processus.
    broken = _overlay(tmp_path, "cassee", "raise ImportError('DLL load failed while importing _C')\n")
    measured = gpu.probe(broken)
    assert measured["error"].startswith("import torch : ImportError: DLL load failed")
    # Surcouche importée, mais torch sans CUDA.
    cpu = _overlay(tmp_path, "processeur", "__version__ = '2.14.0+cpu'\nclass version:\n    cuda = None\n"
                   "class cuda:\n    is_available = staticmethod(lambda: False)\n")
    measured = gpu.probe(cpu)
    assert measured["from_overlay"] is True and "sans CUDA" in gpu.probe_verdict(measured)


# -- ligne de commande et démarrage ----------------------------------------------------------------------


@pytest.fixture
def profile(tmp_path, monkeypatch):
    for key, value in standalone.environment(tmp_path / "profil").items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    return standalone.paths()


def test_the_cli_reports_and_refuses_in_json(profile, capsys, monkeypatch) -> None:
    from apex.standalone.launcher import main

    monkeypatch.setattr(gpu.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    assert main(["gpu", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["driver"]["found"] is False and status["installed"] is None
    assert status["claims"] == "aucune compatibilité GPU n'est affirmée sans sonde réussie sur ce PC"
    assert main(["gpu", "install"]) == gpu.EXIT_REFUSED
    assert "nvidia-smi introuvable" in json.loads(capsys.readouterr().out)["reason"]
    assert main(["gpu", "disable"]) == 0 and gpu.enabled(profile.home) is False
    assert main(["gpu", "enable"]) == 0 and gpu.enabled(profile.home) is True
    capsys.readouterr()
    assert main(["gpu", "verify"]) == gpu.EXIT_REFUSED
    assert main(["gpu", "remove"]) == 0


def test_serve_activates_the_component_before_anything_else(profile, monkeypatch) -> None:
    from apex.cli import main as cli
    from apex.standalone import launcher

    order: list[str] = []
    def activate(_home):
        order.append("gpu")
        return {"state": "absent", "reason": "-"}

    monkeypatch.setattr(gpu, "activate", activate)

    def apex_main(argv):
        order.append(argv[0])
        return 1  # schéma refusé : le démarrage s'arrête là

    monkeypatch.setattr(cli, "main", apex_main)
    assert launcher.serve() == launcher.EXIT_DB_SCHEMA
    assert order == ["gpu", "db"]


# -- générateur des listes figées -------------------------------------------------------------------------


def _lock_gpu():
    spec = importlib.util.spec_from_file_location("lock_gpu", GPU_DIR / "lock_gpu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel(requires: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("torch/__init__.py", "x" * 1000)
        archive.writestr("torch-2.14.0+cu128.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: torch\nVersion: 2.14.0+cu128\n"
                         + "".join(f"Requires-Dist: {r}\n" for r in requires))
    return buffer.getvalue()


@pytest.fixture
def index(monkeypatch):
    """Un index PyTorch local (HTTP sur 127.0.0.1) : une roue Windows cu128, rien pour cu130."""
    files: dict[str, bytes] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")

    def publish(wheel: bytes, *, sha: str | None = None):
        name = "torch-2.14.0%2Bcu128-cp312-cp312-win_amd64.whl"
        files[f"/files/{name}"] = wheel
        digest = sha or hashlib.sha256(wheel).hexdigest()
        files["/cu128/torch/"] = (f'<a href="/files/{name}#sha256={digest}">torch-2.14.0+cu128-cp312-cp312-'
                                  'win_amd64.whl</a>\n<a href="/files/torch-2.14.0%2Bcu128-cp312-cp312-'
                                  'manylinux_2_28_x86_64.whl">linux</a>').encode()
        files["/cu130/torch/"] = b'<a href="/files/torch-2.14.0%2Bcu130-cp313-cp313-win_amd64.whl">x</a>'
        files["/cu126/torch/"] = b""

    yield f"http://127.0.0.1:{server.server_address[1]}", publish
    server.shutdown()


def _spec_dir(tmp_path) -> Path:
    folder = tmp_path / "gpu"
    folder.mkdir()
    (folder / "gpu-runtime.json").write_text(
        (GPU_DIR / "gpu-runtime.json").read_text(encoding="utf-8"), encoding="utf-8")
    return folder


def test_the_lock_generator_pins_only_a_verified_windows_wheel(tmp_path, index, capsys) -> None:
    url, publish = index
    wheel = _wheel(["filelock", "sympy>=1.13.3", 'nvidia-cudnn-cu12==9.0; platform_system == "Linux"',
                    'optree>=0.13.0; extra == "optree"'])
    publish(wheel)
    folder = _spec_dir(tmp_path)
    code = _lock_gpu().main(["--spec-dir", str(folder), "--base-lock",
                             str(BASE_LOCK), "--index", url])
    report = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert code == 1  # deux variantes absentes de cet index : dit, jamais remplacées
    assert report["cu128"]["ok"] and report["cu128"]["requires"] == ["filelock", "sympy>=1.13.3"]
    assert "absent de l'index" in report["cu130"]["reason"] and not (folder / "torch-cu130.lock").exists()
    lock = (folder / "torch-cu128.lock").read_text(encoding="utf-8")
    assert f"--hash=sha256:{hashlib.sha256(wheel).hexdigest()}" in lock and "win_amd64.whl" in lock
    spec = json.loads((folder / "gpu-runtime.json").read_text(encoding="utf-8"))
    cu128 = next(v for v in spec["variants"] if v["id"] == "cu128")
    assert cu128["wheel_bytes"] == len(wheel) and cu128["installed_bytes"] > 1000
    assert [v["prepared"] for v in gpu.variants(spec, folder)] == [False, True, False]


@pytest.mark.parametrize(("requires", "sha", "reason"), [
    (["unpaquet-inconnu>=1"], None, "dépendances non satisfaites"),
    (["sympy>=99"], None, "dépendances non satisfaites"),
    (["filelock"], "0" * 64, "empreinte de l'index"),
])
def test_the_lock_generator_refuses_a_wheel_it_cannot_vouch_for(tmp_path, index, capsys, requires, sha,
                                                                reason) -> None:
    url, publish = index
    publish(_wheel(requires), sha=sha)
    folder = _spec_dir(tmp_path)
    before = (folder / "gpu-runtime.json").read_text(encoding="utf-8")
    code = _lock_gpu().main(["cu128", "--spec-dir", str(folder), "--base-lock",
                             str(BASE_LOCK), "--index", url])
    [result] = json.loads(capsys.readouterr().out)
    assert code == 1 and result["ok"] is False and reason in result["reason"]
    assert not (folder / "torch-cu128.lock").exists()
    assert (folder / "gpu-runtime.json").read_text(encoding="utf-8") == before


def test_the_lock_generator_never_fetches_over_plain_http_elsewhere() -> None:
    with pytest.raises(ValueError, match="https seulement"):
        _lock_gpu()._check_url("http://download.pytorch.org/whl/cu128/torch/")


def test_the_api_reports_what_the_last_start_measured(profile, no_torch) -> None:
    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token

    gpu.activate(profile.home)
    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {get_or_create_api_token()}"}
    data = client.get("/api/v1/assistant/gpu", headers=headers).json()["data"]
    assert data["component"]["state"] == "absent" and "processeur" in data["component"]["reason"]
    assert "lease_held" in data  # le partage du GPU reste rapporté à côté
