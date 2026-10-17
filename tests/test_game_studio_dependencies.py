"""Provisioning safety and shared discovery; no fake generation qualification."""

from pathlib import Path
from unittest.mock import patch

import pytest

from apex.game_graphics.software import discover_blender
from apex.game_studio.__main__ import main
from apex.game_studio.dependencies import install_planner


def test_invalid_explicit_blender_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv("APEX_BLENDER_EXECUTABLE", str(tmp_path / "missing"))
    with patch("apex.game_graphics.software.shutil.which", side_effect=AssertionError("fallback")):
        assert discover_blender()["reason"] == "invalid_blender_executable"


def test_explicit_path_with_spaces(monkeypatch, tmp_path):
    executable = tmp_path / "Blender installed" / "blender"
    executable.parent.mkdir()
    executable.write_text("fixture, never executed")
    executable.chmod(0o700)
    monkeypatch.setenv("APEX_BLENDER_EXECUTABLE", str(executable))
    assert discover_blender()["path"] == str(executable)


def test_windows_standard_locations_version_sort(monkeypatch, tmp_path):
    monkeypatch.delenv("APEX_BLENDER_EXECUTABLE", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    for version in ["4.9", "4.10", "4.11 beta"]:
        p = tmp_path / "Blender Foundation" / f"Blender {version}" / "blender.exe"
        p.parent.mkdir(parents=True)
        p.touch()
    with (
        patch("apex.game_graphics.software.sys.platform", "win32"),
        patch("apex.game_graphics.software.shutil.which", return_value=None),
    ):
        assert "Blender 4.10" in discover_blender()["path"]


@pytest.mark.parametrize("download,license", [(False, False), (True, False), (False, True)])
def test_no_download_or_directory_without_both_consents(download, license, tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_HOME", str(tmp_path / "new-profile"))
    with (
        patch("apex.game_graphics.models.fetch_coder", side_effect=AssertionError("network")),
        pytest.raises(ValueError, match="consent_required"),
    ):
        install_planner(accept_download=download, accept_license=license)
    assert not (tmp_path / "new-profile").exists()


def test_missing_server_blocks_download(monkeypatch, tmp_path):
    monkeypatch.setenv("APEX_HOME", str(tmp_path))
    with (
        patch("apex.game_studio.dependencies.importlib.util.find_spec", return_value=None),
        patch("apex.game_graphics.models.fetch_coder", side_effect=AssertionError("network")),
        pytest.raises(RuntimeError, match="server_missing"),
    ):
        install_planner(accept_download=True, accept_license=True)


def test_installation_uses_existing_fetch_contract_and_is_repeatable(tmp_path):
    target = tmp_path / "coder-model.json"
    info = {
        "manifest": str(target),
        "server_module_present": True,
        "required_free_bytes": 1,
        "repo": "repo",
        "revision": "rev",
        "sha256": "digest",
        "license": "Apache-2.0",
    }

    def fetch(directory):
        import json

        target.write_text(
            json.dumps(
                {
                    "repo": "repo",
                    "commit": "rev",
                    "sha256": "digest",
                    "license": "Apache-2.0",
                    "license_reviewed_before_download": True,
                    "path": str(tmp_path / "weights"),
                }
            )
        )
        return target

    with (
        patch("apex.game_studio.dependencies.planner_installation", return_value=info),
        patch("apex.game_graphics.models.fetch_coder", side_effect=fetch) as download,
        patch("apex.game_graphics.models.file_sha256", return_value="digest"),
    ):
        assert install_planner(accept_download=True, accept_license=True)["status"] == "installed_verified"
        assert install_planner(accept_download=True, accept_license=True)["status"] == "already_verified"
        assert download.call_count == 1
    assert target.is_file()


def test_dependency_cli_no_consent(capsys):
    assert main(["install-planner"]) == 2
    assert "consent_required" in capsys.readouterr().out


def test_runtime_includes_engineer_import_worker(tmp_path):
    from runtime_build.build import build_runtime_from_worktree

    root = Path(__file__).resolve().parents[1]
    built = tmp_path / "runtime"
    build_runtime_from_worktree(built)
    relative = Path("scripts/game_engineer/import_vehicle.py")
    assert (built / relative).read_bytes() == (root / relative).read_bytes()


def test_present_cuda_does_not_bypass_unqualified_handoff():
    from apex.game_studio.project import GameStudioProjectService

    # This refusal precedes mission creation: no fake GPU workload is executed.
    service = object.__new__(GameStudioProjectService)
    request = {"quality": "prototype", "description": "Une voiture sportive", "compute_backend": "cuda"}
    with (
        patch("apex.game_studio.project.probe", return_value={"cuda": {"available": True}}),
        pytest.raises(RuntimeError, match="qualified_brain_quiescence"),
    ):
        service.create_dynamic(request, [])


def test_invalid_existing_manifest_is_preserved(tmp_path):
    target = tmp_path / "coder-model.json"
    original = '{"repo":"not-the-pinned-model"}'
    target.write_text(original)
    info = {
        "manifest": str(target),
        "server_module_present": True,
        "required_free_bytes": 1,
        "repo": "repo",
        "revision": "revision",
        "sha256": "hash",
        "license": "Apache-2.0",
    }
    with (
        patch("apex.game_studio.dependencies.planner_installation", return_value=info),
        patch("apex.game_graphics.models.fetch_coder", side_effect=AssertionError("overwrite")),
        pytest.raises(RuntimeError, match="preserved"),
    ):
        install_planner(accept_download=True, accept_license=True)
    assert target.read_text() == original


def test_low_disk_prevents_any_weight_download(tmp_path):
    from types import SimpleNamespace

    info = {
        "manifest": str(tmp_path / "coder-model.json"),
        "server_module_present": True,
        "required_free_bytes": 100,
    }
    with (
        patch("apex.game_studio.dependencies.planner_installation", return_value=info),
        patch("apex.game_studio.dependencies.shutil.disk_usage", return_value=SimpleNamespace(free=99)),
        patch("apex.game_graphics.models.fetch_coder", side_effect=AssertionError("network")),
        pytest.raises(RuntimeError, match="insufficient_disk_space"),
    ):
        install_planner(accept_download=True, accept_license=True)
