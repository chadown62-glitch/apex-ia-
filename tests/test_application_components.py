"""Composants de l'application dans `GET /api/v1/status` (et donc le Panel) : Cerveau APEX, moteur intégré,
GPU CUDA, Game Studio, planificateur, Blender, Game Graphics, Game Engineer.

Chaque état vient d'une vraie sonde. La sonde de Game Studio appartient à Codex ; ici, son RAPPORT est
fourni, pour vérifier la traduction en états. Règles vérifiées :
- un composant présent mais jamais qualifié sur ce PC est « degraded », jamais « ready » ;
- un composant absent dit pourquoi ;
- aucun de ces composants n'entre dans le verdict global : APEX répond sans GPU ni Game Studio.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import types

import pytest

from apex.obs import component_checks as cc

APPLICATION = ("apex_brain", "native_model", "gpu", "game_studio", "planner", "blender", "game_graphics",
               "game_engineer")


@pytest.fixture(autouse=True)
def _fresh_cache():
    cc._cache.clear()
    yield
    cc._cache.clear()


def _studio(monkeypatch, report=None, absent=None):
    monkeypatch.setattr(cc, "_game_studio", lambda: (absent, report or {}))


def _report(*, family="blocked_dependency", manifest=False, llama=False, native=False,
            blender="/x/blender", workers=True, deps=True, environment="blocked_dependency"):
    return {
        "vehicle_blender": {"status": family, "blender": blender, "workers_present": workers,
                            "coder_manifest_present": manifest, "llama_cpp_present": llama,
                            "apex_native_coder_present": native,
                            "python_dependencies": {"psutil": deps, "numpy": True, "PIL": True}},
        "building_blender": {"status": family}, "chair_blender": {"status": family},
        "environment": {"status": environment},
        "game_engineer": {"status": "available_unqualified",
                          "reason": "UE5 gameplay and physics remain blocked"},
        "cuda": {"available": False},
    }


def test_the_application_components_are_reported_and_never_decide_the_verdict(monkeypatch) -> None:
    for name in APPLICATION:
        assert name in cc.CHECKS
        assert name in cc.INFORMATIONAL and name in cc.STANDALONE_INFORMATIONAL
    monkeypatch.setenv("APEX_PROFILE", "standalone")
    assert set(APPLICATION) <= cc.informational()


def test_the_real_shipped_brain_is_ready() -> None:
    status = cc.check_apex_brain()
    assert status.state == cc.READY and "apex-brain-v2" in status.detail


def test_without_game_studio_every_studio_row_says_it_is_not_installed(monkeypatch) -> None:
    _studio(monkeypatch, absent="Game Studio ne fait pas partie de cette installation d'APEX")
    for check in (cc.check_game_studio, cc.check_planner, cc.check_game_graphics, cc.check_game_engineer):
        status = check()
        assert status.state == cc.OFFLINE and "ne fait pas partie" in status.detail


@pytest.mark.parametrize(("report", "state", "words"), [
    (_report(), cc.OFFLINE, "planificateur"),
    # Le moteur intégré ne planifie pas les missions du Cerveau.
    (_report(native=True, environment="available_unqualified", family="available_unqualified"),
     cc.OFFLINE, "Qwen2.5-Coder-7B épinglé"),
    (_report(manifest=True, llama=True, environment="available_unqualified",
             family="available_unqualified"),
     cc.DEGRADED, "aucune mission encore réussie"),
    (_report(manifest=True, llama=True, environment="available_unqualified", family="available_validated"),
     cc.READY, "mission réussie sur ce PC"),
])
def test_game_studio_is_ready_only_after_a_mission_passed_here(monkeypatch, report, state, words) -> None:
    import apex.assistant.game_studio as studio

    real = studio.find_spec
    monkeypatch.setattr(studio, "find_spec", lambda name: object() if name == "jsonschema" else real(name))
    _studio(monkeypatch, report)
    status = cc.check_game_studio()
    assert status.state == state and words in status.detail, status


@pytest.mark.parametrize(("report", "state", "words"), [
    (_report(), cc.OFFLINE, "planificateur des missions absent"),
    (_report(manifest=True, llama=True), cc.DEGRADED, "Qwen2.5-Coder-7B épinglé"),
    (_report(native=True), cc.OFFLINE, "ne sert qu'aux créations d'une famille"),
    (_report(manifest=True, llama=True, family="available_validated"), cc.READY, "missions réussies"),
])
def test_the_planner_is_what_game_studio_would_use(monkeypatch, report, state, words) -> None:
    _studio(monkeypatch, report)
    status = cc.check_planner()
    assert status.state == state and words in status.detail, status


def test_a_pinned_manifest_without_llama_cpp_is_not_a_planner(monkeypatch) -> None:
    _studio(monkeypatch, _report(manifest=True))
    status = cc.check_planner()
    assert status.state == cc.OFFLINE and "llama-cpp-python absent" in status.detail


def test_game_graphics_and_game_engineer_follow_the_probe(monkeypatch) -> None:
    _studio(monkeypatch, _report(native=True, family="available_unqualified"))
    assert cc.check_game_graphics().state == cc.DEGRADED
    engineer = cc.check_game_engineer()
    if importlib.util.find_spec("jsonschema") is None:
        assert engineer.state == cc.OFFLINE and "jsonschema" in engineer.detail
    else:
        assert engineer.state == cc.DEGRADED and "UE5" in engineer.detail
    _studio(monkeypatch, _report(deps=False))
    assert "psutil" in cc.check_game_graphics().detail


def test_blender_is_measured_by_running_it(monkeypatch) -> None:
    _studio(monkeypatch, absent="absent")
    if shutil.which("blender"):
        status = cc.check_blender()
        assert status.state == cc.READY and status.detail.startswith("Blender ")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    status = cc.check_blender()
    assert status.state == cc.OFFLINE and "introuvable" in status.detail
    _studio(monkeypatch, {"vehicle_blender": {"blender": "/bin/false"}})
    assert cc.check_blender().state == cc.ERROR  # présent mais ne répond pas : jamais « prêt »


class _Cuda:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def get_device_name(self, _index: int) -> str:
        return "GPU de test"


def test_the_gpu_is_ready_only_when_torch_sees_cuda_in_this_process(monkeypatch) -> None:
    from apex.standalone import gpu

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=_Cuda(True)))
    monkeypatch.setattr(gpu, "last_activation", lambda: {"state": "active", "variant": "cu128"})
    status = cc.check_gpu()
    assert status.state == cc.READY and "GPU de test" in status.detail and "cu128" in status.detail
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=_Cuda(False)))
    inactive = {"state": "inactive", "reason": "pilote insuffisant"}
    monkeypatch.setattr(gpu, "last_activation", lambda: inactive)
    status = cc.check_gpu()
    assert status.state == cc.DEGRADED and "pilote insuffisant" in status.detail
    monkeypatch.setattr(gpu, "last_activation", lambda: None)
    monkeypatch.setattr(gpu, "detect_driver", lambda: {"found": True, "cuda_driver": "12.8", "gpus": [
        {"name": "NVIDIA GeForce RTX de test", "driver_version": "999.01"}]})
    status = cc.check_gpu()
    assert status.state == cc.OFFLINE and "GPU détecté : NVIDIA GeForce RTX de test" in status.detail
    assert "Accélération GPU (facultatif)" in status.detail
    cc._cache.clear()
    monkeypatch.setattr(gpu, "detect_driver", lambda: {"found": False, "reason": "nvidia-smi introuvable"})
    status = cc.check_gpu()
    assert status.state == cc.OFFLINE and "processeur" in status.detail


def test_a_failing_probe_is_an_error_never_a_ready_state(monkeypatch) -> None:
    from apex.standalone import gpu

    def broken():
        raise RuntimeError("fichier illisible")

    monkeypatch.setattr(gpu, "last_activation", broken)
    status = cc.check_gpu()
    assert status.state == cc.ERROR and "illisible" in status.detail
