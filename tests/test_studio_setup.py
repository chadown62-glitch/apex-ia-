"""Composants de Game Studio vus d'APEX (`apex.assistant.studio_setup`) : état, taille, provenance, et
installation par les contrats RÉELS de Game Studio (publiés par Codex le 17/10/2026, `d15fa02`) :
- poids du planificateur : `python -m apex.game_studio install-planner --accept-download
  --accept-apache-2-license`, décrit par `apex.game_studio.dependencies.planner_installation()` ;
- moteur du planificateur : la liste figée facultative `requirements-planner-cpu-windows.lock`.

La sonde, `planner_installation()` et l'exécution des commandes sont des DOUBLES ici (aucun téléchargement,
aucune installation réelle) ; un test exécute pour de vrai la commande de Game Studio sans consentement,
quand Game Studio est installé, pour vérifier qu'elle refuse avant tout réseau.
"""

from __future__ import annotations

import importlib.machinery
import json
import os
import stat
import subprocess
import sys
import types
from importlib.util import find_spec
from pathlib import Path

import pytest

import apex.standalone as standalone
from apex.assistant import studio_setup
from apex.obs import component_checks

ROOT = Path(__file__).resolve().parents[1]
REAL_STUDIO = find_spec("apex.game_studio") is not None
LIBRARIES = {"psutil": True, "numpy": True, "PIL": True}
BASE = "numpy==2.5.3 \\\n    --hash=sha256:00\npyyaml==6.0.3 \\\n    --hash=sha256:11\n"
WHEEL = ("https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.36/"
         "llama_cpp_python-0.3.36-py3-none-win_amd64.whl")
ENGINE = (f"llama-cpp-python @ {WHEEL}#sha256=ae5e \\\n    --hash=sha256:ae5e\n"
          "numpy==2.5.3 \\\n    --hash=sha256:00\ndiskcache==5.6.3 \\\n    --hash=sha256:22\n")


def _report(*, blender=None, manifest=False, llama=False, libraries=None, discovery=None):
    """Rapport de la sonde de Game Studio, aux clés qu'elle publie."""
    vehicle = {"blender": blender, "workers_present": True, "coder_manifest_present": manifest,
               "llama_cpp_present": llama, "apex_native_coder_present": False,
               "python_dependencies": dict(LIBRARIES if libraries is None else libraries)}
    if discovery is not None:
        vehicle["blender_discovery"] = discovery
    return {"vehicle_blender": vehicle, "cuda": {"available": False}}


def _planner(*, server=True, manifest=False):
    """Ce que rend `planner_installation()` de Game Studio (schéma 1)."""
    return {"schema_version": 1, "manifest": "C:/APEX/.apex/game-studio/coder-model.json",
            "manifest_present": manifest, "server_module_present": server,
            "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
            "revision": "13fb94bfda8c8cf22497dc57b78f391a9acb426a", "sha256": "586844ea",
            "license": "Apache-2.0", "download_bytes": 5_444_831_232,
            "required_free_bytes": 5_444_831_232 + 1024**3, "requires_explicit_consent": True,
            "automatic_download": False, "gpu_qualified": False,
            "reason": None if server else "llama_cpp_server_missing_install_verified_platform_binary"}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    for key, value in standalone.environment(tmp_path / "home").items():
        monkeypatch.setenv(key, value)
    component_checks._cache.clear()
    studio_setup._reset_for_tests()
    yield
    component_checks._cache.clear()
    studio_setup._reset_for_tests()


@pytest.fixture
def studio(monkeypatch):
    """Game Studio réel s'il est installé ; sinon un paquet vide (sa sonde vient toujours du test)."""
    if not REAL_STUDIO:
        package = types.ModuleType("apex.game_studio")
        package.__spec__ = importlib.machinery.ModuleSpec("apex.game_studio", None, is_package=True)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, "apex.game_studio", package)


@pytest.fixture
def contract(monkeypatch, studio):
    """Le contrat du planificateur de Game Studio (double) : `planner_installation()` seulement."""

    def publish(info=None, *, fails=None):
        module = types.ModuleType("apex.game_studio.dependencies")

        def planner_installation():
            if fails is not None:
                raise fails
            return info

        module.planner_installation = planner_installation
        monkeypatch.setitem(sys.modules, "apex.game_studio.dependencies", module)

    return publish


@pytest.fixture
def no_contract(monkeypatch, studio):
    monkeypatch.setitem(sys.modules, "apex.game_studio.dependencies", None)  # import → ImportError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Dossier `runtime/` de l'application : liste figée de base, et celle du moteur si on la dépose."""
    folder = tmp_path / "app" / "runtime"
    folder.mkdir(parents=True)
    (folder / "requirements-windows.lock").write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(studio_setup, "_runtime_dir", lambda: folder)
    return folder


class _Runner:
    """Exécution des commandes d'installation (double) : ce qui a été lancé, et ce que la commande rend."""

    def __init__(self, code=0, output=""):
        self.code, self.output, self.commands = code, output, []

    def __call__(self, command, progress):
        self.commands.append(command)
        for line in self.output.splitlines()[:3]:
            progress(line)
        return self.code, self.output


def _by_id(listing):
    return {item["id"]: item for item in listing["items"]}


@pytest.mark.skipif(REAL_STUDIO, reason="Game Studio fait partie de cette installation")
def test_without_game_studio_nothing_is_listed_and_it_is_said() -> None:
    listing = studio_setup.dependencies(probe=lambda: _report())
    assert listing["installed"] is False and listing["items"] == []
    assert "ne fait pas partie de cette installation" in listing["contract"]["reason"]


def test_without_any_contract_every_missing_component_says_why_it_waits(no_contract, runtime,
                                                                        monkeypatch) -> None:
    monkeypatch.setattr(studio_setup, "_declared", lambda _name: False)  # aucune n'est dans la liste figée
    listing = studio_setup.dependencies(probe=lambda: _report(libraries={**LIBRARIES, "PIL": False}))
    items = _by_id(listing)
    assert list(items)[:3] == ["planner_model", "inference_engine", "blender"]
    assert {"python:psutil", "python:numpy", "python:pillow", "python:jsonschema"} <= set(items)
    assert listing["contract"]["planner"] is False and listing["contract"]["engine"] is False
    assert items["planner_model"]["install"]["reason"] == (
        "En attente du composant Game Studio : Game Studio ne publie pas de contrat d'installation du "
        "planificateur")
    assert items["inference_engine"]["install"]["reason"].endswith(
        "ne fournit pas de liste figée du moteur pour Windows")
    assert items["inference_engine"]["source"].startswith("PyPI : sources seulement")
    assert items["blender"]["detail"] == "introuvable dans le PATH" and "PATH" in items["blender"]["manual"]
    for item in listing["items"]:
        assert item["install"]["available"] is False
        if item["state"] != "present":
            assert item["install"]["reason"].startswith(studio_setup.WAITING)
    psutil = items["python:psutil"]
    assert psutil["state"] == "present" and psutil["install"]["reason"] == ""


def test_the_planner_identity_size_and_licence_come_from_game_studios_contract(contract, runtime) -> None:
    contract(_planner(server=False))
    planner = _by_id(studio_setup.dependencies(probe=lambda: _report()))["planner_model"]
    assert planner["bytes"] == 5_444_831_232
    assert planner["source"] == "Hugging Face Qwen/Qwen2.5-Coder-7B-Instruct-GGUF, révision 13fb94bfda8c"
    assert planner["license"].startswith("Apache-2.0")
    # Game Studio refuse sans le serveur local : aucun bouton, et l'étape d'avant est nommée.
    assert planner["install"] == {"available": False, "reason": (
        "En attente du composant Game Studio : installez d'abord le moteur d'inférence du planificateur "
        "(llama-cpp-python)")}


def test_the_planner_is_offered_once_its_engine_is_there_with_an_explicit_question(contract,
                                                                                  runtime) -> None:
    contract(_planner(server=True))
    planner = _by_id(studio_setup.dependencies(probe=lambda: _report(llama=True)))["planner_model"]
    assert planner["install"]["available"] is True
    question = planner["install"]["confirmation"]
    assert "5,4 Go" in question and "licence Apache-2.0" in question and "6,5 Go libres requis" in question


def test_a_present_planner_offers_nothing(contract, runtime) -> None:
    contract(_planner(server=True, manifest=True))
    planner = _by_id(studio_setup.dependencies(probe=lambda: _report(manifest=True, llama=True)))
    assert planner["planner_model"]["state"] == "present"
    assert planner["planner_model"]["install"] == {"available": False, "reason": ""}


def test_a_failing_planner_contract_offers_no_button_and_says_why(contract, runtime) -> None:
    contract(fails=ImportError("No module named 'psutil'"))
    listing = studio_setup.dependencies(probe=lambda: _report())
    assert "contrat du planificateur en échec : ImportError" in listing["contract"]["reason"]
    assert _by_id(listing)["planner_model"]["install"]["available"] is False


def test_the_engine_is_offered_from_game_studios_frozen_list_when_it_changes_nothing(no_contract,
                                                                                    runtime) -> None:
    (runtime / studio_setup.ENGINE_LOCK).write_text(ENGINE, encoding="utf-8")
    engine = _by_id(studio_setup.dependencies(probe=lambda: _report()))["inference_engine"]
    assert engine["install"]["available"] is True
    assert engine["source"].startswith(WHEEL) and "processeur seulement" in engine["source"]
    assert "pas encore qualifiée" in engine["install"]["confirmation"]
    assert "non qualifiée" in engine["detail"]


def test_an_engine_list_that_would_change_apex_libraries_is_refused(no_contract, runtime) -> None:
    (runtime / studio_setup.ENGINE_LOCK).write_text(ENGINE.replace("numpy==2.5.3", "numpy==2.4.0"),
                                                   encoding="utf-8")
    engine = _by_id(studio_setup.dependencies(probe=lambda: _report()))["inference_engine"]
    assert engine["install"]["available"] is False
    assert "changerait des bibliothèques d'APEX : numpy 2.4.0 ≠ 2.5.3" in engine["install"]["reason"]


def test_game_studios_real_engine_list_changes_no_apex_library() -> None:
    """La vraie liste du moteur (Codex, `d15fa02`), quand elle est dans l'arbre : contrainte par la liste de
    base, elle ne doit changer aucune version installée par « Préparer APEX »."""
    if not (ROOT / "setup/packaging/runtime" / studio_setup.ENGINE_LOCK).is_file():
        pytest.skip("liste du moteur absente de cet arbre (branche de Game Studio non fusionnée)")
    offer = studio_setup.engine_offer()
    assert offer["available"] is True, offer["reason"]
    assert offer["source"].startswith("https://github.com/abetlen/llama-cpp-python/releases/download/")


@pytest.mark.parametrize(("declared", "expected", "manual"), [
    (True, "« Préparer APEX » (APEX arrêté) l'installe", True),
    (False, "non déclarée dans la liste figée", False),
    (None, "liste figée des bibliothèques illisible", False),
])
def test_a_missing_library_is_installed_by_prepare_only_once_declared(studio, monkeypatch, declared,
                                                                      expected, manual) -> None:
    monkeypatch.setattr(studio_setup, "_declared", lambda _name: declared)
    item = _by_id(studio_setup.dependencies(
        probe=lambda: _report(libraries={**LIBRARIES, "jsonschema": False})))["python:jsonschema"]
    assert item["state"] == "missing" and expected in item["install"]["reason"]
    assert item["install"]["available"] is False  # « Préparer APEX » est dans le Panel, APEX arrêté
    assert (item["manual"] is not None) is manual
    # Une bibliothèque déjà installable par APEX n'attend rien de Game Studio, et ne le dit pas.
    assert item["install"]["reason"].startswith(studio_setup.WAITING) is not manual


def test_blender_counts_only_when_it_really_answers_and_says_how_it_was_found(studio, tmp_path) -> None:
    script = tmp_path / "blender"
    script.write_text("#!/bin/sh\necho 'Blender 4.3.2'\necho 'build hash: 32f5fdce0a0a'\n",
                      encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    found = {"path": str(script), "source": "ProgramFiles", "reason": None}
    item = _by_id(studio_setup.dependencies(
        probe=lambda: _report(blender=str(script), discovery=found)))["blender"]
    assert item["state"] == "present"
    assert item["detail"] == f"Blender 4.3.2 ({script}, trouvé par Program Files)"
    component_checks._cache.clear()
    silent = _by_id(studio_setup.dependencies(probe=lambda: _report(blender="/bin/false")))["blender"]
    assert silent["state"] == "missing" and "ne répond pas" in silent["detail"]
    invalid = {"path": None, "source": "explicit", "reason": "invalid_blender_executable"}
    wrong = _by_id(studio_setup.dependencies(probe=lambda: _report(discovery=invalid)))["blender"]
    assert wrong["detail"] == "APEX_BLENDER_EXECUTABLE ne désigne pas un exécutable"
    assert "Program Files" in wrong["manual"] and "APEX_BLENDER_EXECUTABLE" in wrong["manual"]


def test_an_install_needs_confirmation_and_a_covered_component(no_contract, runtime) -> None:
    runner = _Runner()
    with pytest.raises(PermissionError, match="non confirmée"):
        studio_setup.start_install("inference_engine", confirmed=False, probe=_report, runner=runner)
    with pytest.raises(LookupError):
        studio_setup.start_install("modele-inconnu", confirmed=True, probe=_report, runner=runner)
    for item in ("planner_model", "inference_engine", "blender"):  # aucun contrat, ou rien à installer
        with pytest.raises(RuntimeError, match="En attente"):
            studio_setup.start_install(item, confirmed=True, probe=_report, runner=runner)
    assert runner.commands == []


def test_the_engine_is_installed_by_the_prepare_mechanism_with_hashes(no_contract, runtime) -> None:
    (runtime / studio_setup.ENGINE_LOCK).write_text(ENGINE, encoding="utf-8")
    runner = _Runner(0, "Successfully installed diskcache-5.6.3 llama-cpp-python-0.3.36")
    job = studio_setup.start_install("inference_engine", confirmed=True, probe=_report, background=False,
                                     runner=runner)
    [command] = runner.commands
    assert command[:4] == [sys.executable, "-P", "-m", "pip"]
    assert {"--require-hashes", "--only-binary=:all:", "--no-deps"} <= set(command)
    assert command[-2:] == ["-r", str(runtime / studio_setup.ENGINE_LOCK)]
    assert job["state"] == "succeeded" and job["log"] == [
        "Successfully installed diskcache-5.6.3 llama-cpp-python-0.3.36"]
    failed = studio_setup.start_install("inference_engine", confirmed=True, probe=_report, background=False,
                                        runner=_Runner(1, "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES"))
    assert failed["state"] == "failed" and "HASHES" in failed["detail"]


def test_the_planner_is_installed_only_by_game_studios_command_with_both_consents(contract,
                                                                                 runtime) -> None:
    contract(_planner(server=True))
    output = json.dumps({"status": "installed_verified", "manifest": "m", "gpu_qualified": False}, indent=2)
    runner = _Runner(0, output)
    job = studio_setup.start_install("planner_model", confirmed=True, probe=lambda: _report(llama=True),
                                     background=False, runner=runner)
    assert runner.commands == [[sys.executable, "-P", "-m", "apex.game_studio", "install-planner",
                                "--accept-download", "--accept-apache-2-license"]]
    assert job["state"] == "succeeded" and job["detail"] == "installé et vérifié par Game Studio"


@pytest.mark.parametrize(("code", "output", "detail"), [
    (2, '{"status": "blocked", "error": "planner_insufficient_disk_space"}',
     "espace disque insuffisant pour le planificateur (planner_insufficient_disk_space)"),
    (2, '{"status": "blocked", "error": "coder_download_integrity_failed"}',
     "le fichier téléchargé n'a pas l'empreinte épinglée : il est refusé "
     "(coder_download_integrity_failed)"),
    (1, "Traceback (most recent call last):\nKeyboardInterrupt", "code 1"),
])
def test_a_refused_planner_install_is_never_reported_as_done(contract, runtime, code, output,
                                                             detail) -> None:
    contract(_planner(server=True))
    job = studio_setup.start_install("planner_model", confirmed=True, probe=lambda: _report(llama=True),
                                     background=False, runner=_Runner(code, "progression…\n" + output))
    assert job["state"] == "failed" and job["detail"] == detail


def test_one_install_at_a_time(no_contract, runtime) -> None:
    (runtime / studio_setup.ENGINE_LOCK).write_text(ENGINE, encoding="utf-8")
    studio_setup._job = {"item": "planner_model", "state": "running", "log": []}
    with pytest.raises(RuntimeError, match="déjà en cours"):
        studio_setup.start_install("inference_engine", confirmed=True, probe=_report, runner=_Runner())


def test_game_studios_real_command_refuses_without_both_consents_before_any_network(tmp_path) -> None:
    """Exécutée pour de vrai quand Game Studio est installé : sans les deux consentements, la commande
    refuse avant tout import réseau ou disque (contrat de Codex)."""
    if not (ROOT / "apex/game_studio/__main__.py").is_file():
        pytest.skip("Game Studio sans commande d'installation dans cet arbre")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "APEX_HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-P", "-m", "apex.game_studio", "install-planner"],
                          capture_output=True, text=True, env=env, timeout=120, check=False)
    assert done.returncode == 2
    assert json.loads(done.stdout)["error"] == "planner_download_and_apache_license_consent_required"
    assert not (tmp_path / ".apex").exists()  # rien d'écrit


def test_the_api_lists_and_refuses_in_plain_words() -> None:
    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token

    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {get_or_create_api_token()}"}
    listing = client.get("/api/v1/assistant/game-studio/dependencies", headers=headers)
    assert listing.status_code == 200 and "contract" in listing.json()["data"]
    refused = client.post("/api/v1/assistant/game-studio/dependencies/planner_model/install",
                          headers=headers, json={})
    assert refused.status_code == 400 and "non confirmée" in refused.json()["detail"]
    unknown = client.post("/api/v1/assistant/game-studio/dependencies/rien/install", headers=headers,
                          json={"confirm": True})
    assert unknown.status_code in (404, 409)  # inconnu ; ou, sans Game Studio, rien n'est installable
