"""Contract/selection tests. Hypothetical planning inputs are not UE execution benchmarks."""

import copy
from pathlib import Path

import pytest

from apex.game_graphics.ue.contract import SceneCriteria, ScenePlan
from apex.game_graphics.ue.project import prepare, refuse_personal_project
from apex.game_graphics.ue.selection import GraphicsTask, select_method


@pytest.mark.parametrize("field,value", [("python", "print('bad')"), ("console", "r.SomeCommand 1")])
def test_scene_rejects_executable_model_fields(field, value):
    data = ScenePlan.demo().to_dict()
    data[field] = value
    with pytest.raises(ValueError, match="unexpected_scene_fields"):
        ScenePlan.parse(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "blueprint"),
        ("owner", "engineer"),
        ("shape", "/Game/PersonalAsset"),
        ("dimensions_cm", [-1, 20, 30]),
        ("location_cm", [float("nan"), 0, 0]),
        ("id", "../../other"),
    ],
)
def test_artistic_operations_reject_unsafe_or_gameplay_requests(field, value):
    data = ScenePlan.demo().to_dict()
    data["actors"][0][field] = value
    with pytest.raises(ValueError):
        ScenePlan.parse(data)


def test_identifiers_materials_and_engineer_dependencies():
    data = ScenePlan.demo().to_dict()
    duplicate = copy.deepcopy(data)
    duplicate["actors"][1]["id"] = duplicate["actors"][0]["id"]
    with pytest.raises(ValueError, match="duplicate_scene_identifier"):
        ScenePlan.parse(duplicate)
    data["actors"][0]["material_id"] = "unknown"
    with pytest.raises(ValueError, match="unresolved_material_reference"):
        ScenePlan.parse(data)
    data = ScenePlan.demo().to_dict()
    data["protected_engineer_ids"] = ["road"]
    with pytest.raises(ValueError, match="engineer_identifier_collision"):
        ScenePlan.parse(data)


@pytest.mark.parametrize("level", ["prototype", "jouable", "soigne", "photorealiste"])
def test_quality_requirements_and_overrides_are_preserved(level):
    criteria = SceneCriteria.for_level(level, max_attempts_per_defect=2, position_tolerance_cm=0.1)
    assert criteria.level == level and criteria.max_attempts_per_defect == 2
    if level in ("soigne", "photorealiste"):
        assert criteria.calibrated_reference_required and criteria.texture_maps_required
    if level != "prototype":
        assert criteria.min_fps is not None


@pytest.mark.parametrize(
    "path", [r"C:\GameProject", r"c:\gameproject\Content", "/mnt/c/GameProject/Maps", r"\\?\C:\GameProject"]
)
def test_protected_project_spellings_refused(path):
    with pytest.raises(ValueError, match="protected_personal_project"):
        refuse_personal_project(Path(path))


@pytest.mark.parametrize(
    "kind,final,complex_mesh,method",
    [
        ("vehicle", False, False, "blender"),
        ("uv_retopology", False, False, "blender"),
        ("vehicle", True, True, "hybrid"),
        ("environment", True, False, "unreal"),
        ("lighting", True, False, "unreal"),
        ("pcg", True, False, "unreal"),
    ],
)
def test_method_matches_required_work_and_destination(kind, final, complex_mesh, method):
    capabilities = {"blender": {"available": True}, "unreal": {"available": False}, "features": {}}
    choice = select_method(GraphicsTask(kind, final, complex_mesh), capabilities)
    assert choice["method"] == method
    assert choice["status"] == "blocked"
    assert choice["silent_fallback_allowed"] is False


def test_costs_choose_only_qualified_compatible_alternatives_within_budget():
    # Deliberate control-plane fixture, not measurements taken from Unreal.
    capabilities = {
        "blender": {"available": True},
        "unreal": {"available": True},
        "features": {"modeling_tools": {"unreal": True, "hybrid": True}},
    }
    common = {
        "status": "measured",
        "task_kind": "modeling_tools",
        "level": "prototype",
        "peak_ram_mb": 10000,
    }
    costs = {
        "unreal": {**common, "peak_gpu_mb": 9000, "elapsed_s": 1},
        "hybrid": {**common, "peak_gpu_mb": 6000, "elapsed_s": 20},
    }
    choice = select_method(GraphicsTask("modeling_tools", True), capabilities, costs=costs)
    assert choice["method"] == "hybrid"
    assert choice["status"] == "selected"
    assert not choice["candidates"][0]["within_budget"]


def test_scene_version_requires_matching_parent_and_protected_dependencies(tmp_path):
    data = ScenePlan.demo().to_dict()
    data.update(revision=2, parent_manifest_sha256=None)
    with pytest.raises(ValueError, match="unverified_scene_parent"):
        ScenePlan.parse(data)
    data.update(parent_manifest_sha256="a" * 64)
    with pytest.raises(ValueError, match="scene_parent_receipt_unverified"):
        prepare(tmp_path / "new", ScenePlan.parse(data))
    data = ScenePlan.demo().to_dict()
    data["protected_engineer_ids"] = ["vehicle_controller"]
    with pytest.raises(ValueError, match="protected_engineer_world"):
        prepare(tmp_path / "other", ScenePlan.parse(data))
