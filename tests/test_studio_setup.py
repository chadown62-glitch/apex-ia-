"""Composants de Game Studio vus d'APEX (`apex.assistant.studio_setup`) : état, taille, provenance, et
installation offerte SEULEMENT quand Game Studio en publie le contrat (`apex.game_studio.setup`).

La sonde de Game Studio est fournie par chaque test (son rapport est le format de
`apex.game_studio.capabilities.probe`, propriété de Codex). Le contrat d'installation est un DOUBLE : Game
Studio n'en publie pas encore (17/10/2026). Ces tests prouvent qu'APEX n'offre un bouton que pour ce que le
contrat couvre, et ce qu'il dit sinon ; ils ne prouvent aucune installation réelle.
"""

from __future__ import annotations

import importlib.machinery
import stat
import sys
import types
from importlib.util import find_spec

import pytest

import apex.standalone as standalone
from apex.assistant import studio_setup
from apex.obs import component_checks

REAL_STUDIO = find_spec("apex.game_studio") is not None
LIBRARIES = {"psutil": True, "numpy": True, "PIL": True}


def _report(*, blender=None, manifest=False, llama=False, libraries=None):
    """Rapport de la sonde de Game Studio, aux clés qu'elle publie."""
    return {"vehicle_blender": {"blender": blender, "workers_present": True,
                                "coder_manifest_present": manifest, "llama_cpp_present": llama,
                                "apex_native_coder_present": False,
                                "python_dependencies": dict(LIBRARIES if libraries is None else libraries)},
            "cuda": {"available": False}}


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


class _Contract:
    """Double du contrat `apex.game_studio.setup` : ce qu'il offre, et ce que fait son installation."""

    def __init__(self, offers, *, fails=None):
        self.offers, self.fails, self.calls = offers, fails, []

    def plan(self):
        if isinstance(self.offers, BaseException):
            raise self.offers
        return self.offers

    def install(self, item_id, *, confirmed, progress):
        self.calls.append((item_id, confirmed))
        progress("téléchargement… 100 %")
        progress("empreinte SHA-256 vérifiée")
        if self.fails:
            raise self.fails
        return {"ok": True, "detail": "installé et vérifié"}


@pytest.fixture
def contract(monkeypatch, studio):
    def publish(offers, **kwargs):
        double = _Contract(offers, **kwargs)
        module = types.ModuleType("apex.game_studio.setup")
        module.plan, module.install = double.plan, double.install
        monkeypatch.setitem(sys.modules, "apex.game_studio.setup", module)
        return double

    return publish


def _by_id(listing):
    return {item["id"]: item for item in listing["items"]}


@pytest.mark.skipif(REAL_STUDIO, reason="Game Studio fait partie de cette installation")
def test_without_game_studio_nothing_is_listed_and_it_is_said() -> None:
    listing = studio_setup.dependencies(probe=lambda: _report())
    assert listing["installed"] is False and listing["items"] == []
    assert "ne fait pas partie de cette installation" in listing["contract"]["reason"]


def test_each_missing_component_says_its_size_provenance_and_why_it_waits(studio, monkeypatch) -> None:
    monkeypatch.setattr(studio_setup, "_declared", lambda _name: False)  # aucune n'est dans la liste figée
    listing = studio_setup.dependencies(probe=lambda: _report(libraries={**LIBRARIES, "PIL": False}))
    items = _by_id(listing)
    assert list(items)[:3] == ["planner_model", "inference_engine", "blender"]
    assert {"python:psutil", "python:numpy", "python:pillow", "python:jsonschema"} <= set(items)
    assert listing["contract"]["present"] is False
    assert "contrat d'installation" in listing["contract"]["reason"]
    planner = items["planner_model"]
    assert planner["state"] == "missing" and planner["bytes"] == 5_444_831_232
    assert planner["license"].startswith("Apache-2.0")
    # Identité du modèle : lue dans le code de Game Studio quand il est là, jamais devinée sinon.
    assert planner["source"] is None or "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF" in planner["source"]
    assert items["inference_engine"]["source"].startswith("PyPI : sources seulement")
    assert items["blender"]["detail"] == "introuvable dans le PATH" and "PATH" in items["blender"]["manual"]
    assert items["python:pillow"]["state"] == "missing"
    for item in (planner, items["inference_engine"], items["blender"], items["python:pillow"]):
        assert item["install"] == {"available": False, "reason": item["install"]["reason"]}
        assert item["install"]["reason"].startswith("En attente du composant Game Studio : ")
    psutil = items["python:psutil"]
    assert psutil["state"] == "present" and psutil["install"]["reason"] == ""


def test_the_unprobed_jsonschema_follows_the_frozen_list() -> None:
    """`jsonschema`, que la sonde de Game Studio ne vérifie pas : son état et son installation suivent la
    liste figée réelle d'APEX (absente le 17/10/2026)."""
    declared = studio_setup._declared("jsonschema")
    pytest.importorskip("apex.game_studio", reason="Game Studio absent : rien n'est listé")
    item = _by_id(studio_setup.dependencies(probe=lambda: _report()))["python:jsonschema"]
    if item["state"] == "missing":
        expected = "« Préparer APEX »" if declared else "non déclarée dans la liste figée"
        assert expected in item["install"]["reason"]


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


def test_blender_counts_only_when_it_really_answers(studio, tmp_path) -> None:
    script = tmp_path / "blender"
    script.write_text("#!/bin/sh\necho 'Blender 4.3.2'\necho 'build hash: 32f5fdce0a0a'\n",
                      encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    item = _by_id(studio_setup.dependencies(probe=lambda: _report(blender=str(script))))["blender"]
    assert item["state"] == "present" and item["detail"] == f"Blender 4.3.2 ({script})"
    assert item["install"]["reason"] == ""
    component_checks._cache.clear()
    silent = _by_id(studio_setup.dependencies(probe=lambda: _report(blender="/bin/false")))["blender"]
    assert silent["state"] == "missing" and "ne répond pas" in silent["detail"]


def test_present_components_offer_nothing_to_install(studio, contract) -> None:
    contract([{"id": "planner_model", "available": True}])
    items = _by_id(studio_setup.dependencies(probe=lambda: _report(manifest=True, llama=True)))
    assert items["planner_model"]["state"] == "present" and items["inference_engine"]["state"] == "present"
    assert items["planner_model"]["install"]["available"] is False


def test_a_button_exists_only_for_what_the_contract_covers(studio, contract) -> None:
    contract([
        {"id": "planner_model", "available": True, "bytes": 5_444_831_232,
         "source": "Hugging Face Qwen/Qwen2.5-Coder-7B-Instruct-GGUF@13fb94bf", "license": "Apache-2.0",
         "confirmation": "Télécharger 5,4 Go sous licence Apache-2.0 ?"},
        {"id": "inference_engine", "available": False, "reason": "roue CUDA en préparation"},
    ])
    listing = studio_setup.dependencies(probe=lambda: _report())
    items = _by_id(listing)
    assert listing["contract"] == {"present": True, "module": "apex.game_studio.setup", "reason": ""}
    question = "Télécharger 5,4 Go sous licence Apache-2.0 ?"
    assert items["planner_model"]["install"] == {"available": True, "reason": "", "confirmation": question}
    assert items["planner_model"]["source"].endswith("@13fb94bf")
    assert items["inference_engine"]["install"] == {
        "available": False, "reason": "En attente du composant Game Studio : roue CUDA en préparation"}
    assert items["blender"]["install"]["available"] is False  # non couvert par le contrat


def test_a_broken_contract_offers_no_button_and_says_why(studio, contract) -> None:
    contract(RuntimeError("index illisible"))
    listing = studio_setup.dependencies(probe=lambda: _report())
    assert "plan() de Game Studio a échoué : RuntimeError: index illisible" in listing["contract"]["reason"]
    assert not any(i["install"]["available"] for i in listing["items"])


def test_a_failing_probe_counts_nothing_as_present(studio) -> None:
    def broken():
        raise OSError("sonde cassée")

    listing = studio_setup.dependencies(probe=broken)
    assert listing["items"] == [] and "sonde cassée" in listing["problem"]


def test_an_install_needs_confirmation_a_contract_and_a_covered_component(studio, contract) -> None:
    probe = lambda: _report()  # noqa: E731
    with pytest.raises(PermissionError, match="non confirmée"):
        studio_setup.start_install("planner_model", confirmed=False, probe=probe)
    with pytest.raises(RuntimeError, match="En attente du composant Game Studio"):  # aucun contrat
        studio_setup.start_install("planner_model", confirmed=True, probe=probe)
    double = contract([{"id": "planner_model", "available": True}])
    with pytest.raises(LookupError):
        studio_setup.start_install("modele-inconnu", confirmed=True, probe=probe)
    with pytest.raises(RuntimeError, match="En attente"):  # présent dans la liste, pas dans le contrat
        studio_setup.start_install("blender", confirmed=True, probe=probe)
    assert double.calls == []


def test_an_install_runs_the_contract_once_and_reports_its_own_log(studio, contract) -> None:
    double = contract([{"id": "planner_model", "available": True}])
    job = studio_setup.start_install("planner_model", confirmed=True, probe=lambda: _report(),
                                     background=False)
    assert double.calls == [("planner_model", True)]
    assert job["state"] == "succeeded" and job["detail"] == "installé et vérifié"
    assert job["log"] == ["téléchargement… 100 %", "empreinte SHA-256 vérifiée"]
    assert studio_setup.dependencies(probe=lambda: _report())["job"]["state"] == "succeeded"


def test_a_failed_install_is_never_reported_as_done(studio, contract) -> None:
    contract([{"id": "planner_model", "available": True}], fails=OSError("disque plein"))
    job = studio_setup.start_install("planner_model", confirmed=True, probe=lambda: _report(),
                                     background=False)
    assert job["state"] == "failed" and job["detail"] == "OSError: disque plein"


def test_one_install_at_a_time(studio, contract) -> None:
    contract([{"id": "planner_model", "available": True}, {"id": "inference_engine", "available": True}])
    studio_setup._job = {"item": "planner_model", "state": "running", "log": []}
    with pytest.raises(RuntimeError, match="déjà en cours"):
        studio_setup.start_install("inference_engine", confirmed=True, probe=lambda: _report())


def test_the_api_lists_and_refuses_in_plain_words(tmp_path) -> None:
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
