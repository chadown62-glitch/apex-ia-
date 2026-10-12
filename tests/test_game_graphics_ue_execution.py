"""Actual local files/process cleanup and honest missing-engine gates; no mocked unreal API."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apex.game_graphics.ue.__main__ import run_scene
from apex.game_graphics.ue.capabilities import probe
from apex.game_graphics.ue.contract import SceneCriteria, ScenePlan
from apex.game_graphics.ue.execution import run_editor, stop_owned
from apex.game_graphics.ue.judge import UnrealGraphicsOracle, compare_defects, compare_reference_views
from apex.game_graphics.ue.project import SCRIPTS, prepare


def missing_engine():
    return {"unreal": {"available": False, "editor": None}, "blender": {"available": True}, "features": {}}


def test_actual_preparation_has_no_umap_or_native_capture(tmp_path):
    root = tmp_path / "scene"
    result = run_scene(
        ScenePlan.demo(), root, missing_engine(), SceneCriteria.for_level("prototype"), execute=True
    )
    assert result["status"] == "blocked" and result["reason"] == "unreal_editor_missing"
    assert result["engine_executed"] is False and result["captures_created"] is False
    project = root / "attempt-01/project"
    assert (project / "ApexGraphics.uproject").is_file()
    assert (project / "Tools/ue_build_scene.py").is_file()
    assert not list(root.rglob("*.umap")) and not list(root.rglob("*.png"))
    manifest = json.loads(Path(result["scene_asset_contract"]).read_text())
    assert not manifest["engine_created"] and not manifest["engine_judged"]
    assert manifest["geometry_method"] == "not_generated"
    with pytest.raises(ValueError, match="output_exists"):
        run_scene(
            ScenePlan.demo(), root, missing_engine(), SceneCriteria.for_level("prototype"), execute=True
        )


def test_missing_editor_never_spawns_a_process(tmp_path, monkeypatch):
    def forbidden_spawn(*args, **kwargs):
        pytest.fail("no substitute process may stand in for Unreal")

    monkeypatch.setattr(subprocess, "Popen", forbidden_spawn)
    with pytest.raises(RuntimeError, match="unreal_editor_missing_no_execution"):
        run_editor(missing_engine(), tmp_path, "build")


def test_producer_render_claim_cannot_pass_without_actual_engine_execution(tmp_path):
    claimed = {"status": "created", "render_pass": True, "aesthetic_qualified": True}
    inspection = {"status": "inspected", "reference_calibrated": True, "actors": {}}
    oracle = UnrealGraphicsOracle(
        tmp_path, ScenePlan.demo(), SceneCriteria.for_level("photorealiste"), claimed, inspection
    )
    assert oracle.judge(sandbox_result=None, task=None).verdict.value == "unknown"


def test_native_references_are_not_compared_without_actual_editor(tmp_path):
    manifest = tmp_path / "references.json"
    manifest.write_text("[]")
    result = compare_reference_views(tmp_path, manifest)
    assert result["status"] == "blocked" and result["calibrated"] is False


def test_revision_receipt_and_old_files_are_preserved(tmp_path):
    from apex.game_graphics.models import file_sha256

    root = tmp_path / "first"
    prepare(root, ScenePlan.demo())
    parent = root / "scene-contract.json"
    old_hash = file_sha256(parent)
    data = ScenePlan.demo().to_dict()
    data.update(revision=2, parent_manifest_sha256=old_hash)
    next_root = tmp_path / "second"
    manifest = prepare(next_root, ScenePlan.parse(data), parent=parent)
    assert manifest["parent_manifest_sha256"] == old_hash
    assert file_sha256(parent) == old_hash
    assert manifest["scene_id"] == "urban_demo" and manifest["revision"] == 2


def test_integrated_engineer_level_cannot_be_rebuilt_as_an_empty_graphics_level(tmp_path):
    from apex.game_graphics.models import file_sha256

    root = tmp_path / "first"
    prepare(root, ScenePlan.demo())
    parent = root / "scene-contract.json"
    old = json.loads(parent.read_text())
    old["engineer_integrated"] = True
    parent.write_text(json.dumps(old))
    data = ScenePlan.demo().to_dict()
    data.update(revision=2, parent_manifest_sha256=file_sha256(parent))
    with pytest.raises(ValueError, match="functional_revalidation"):
        prepare(tmp_path / "second", ScenePlan.parse(data), parent=parent)


def test_precise_defects_stop_on_regression_or_no_improvement():
    before = [{"id": "pose", "actor": "road", "origin": "unreal", "error": 12}]
    assert compare_defects(before, before) == "no_improvement"
    assert compare_defects(before, [{**before[0], "error": 2}]) is None
    assert (
        compare_defects(before, [{"id": "material", "actor": "building", "error": 1}])
        == "regression_detected"
    )


def test_process_cleanup_uses_real_linux_processes_without_testing_unreal():
    if os.name == "nt":
        pytest.skip("Windows process-tree cleanup requires actual Windows qualification")
    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    peer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        stop_owned(owned)
        assert owned.poll() is not None
        assert peer.poll() is None
    finally:
        stop_owned(owned)
        stop_owned(peer)


def test_engine_scripts_are_syntax_checked_without_importing_a_fake_unreal_module():
    for name in ("ue_build_scene.py", "ue_inspect_scene.py"):
        source = (SCRIPTS / name).read_text()
        ast.parse(source)
        assert "import unreal" in source
        assert "protected_personal_project" in source
        assert "APEX_OWNER=engineer" in source


def test_actual_capability_observations_never_claim_unreal_qualification():
    facts = probe()
    assert facts["unreal"]["engine_executed"] is False
    assert facts["unreal"]["qualified"] is False
    assert facts["gpu_measurement"] == "unknown"
    assert not any(f.get("unreal") for f in facts["features"].values())
