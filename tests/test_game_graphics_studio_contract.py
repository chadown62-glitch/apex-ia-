"""The LLM boundary must reject code, permission expansion and unbounded mesh edits."""

import copy

import pytest

from apex.game_graphics.studio.contract import parse_plan
from apex.game_graphics.studio.execution import store_plan


def plan():
    return {
        "family": "chair",
        "operations": [
            {"op": "set_location", "target": "arm_left", "params": {"location_m": [0, 0, 0.12]}},
            {"op": "save_revision_and_render", "target": "scene", "params": {}},
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(code='__import__("os").system("anything")'),
        lambda p: p["operations"][0].update(op="execute_python"),
        lambda p: p["operations"][0].update(target="../outside"),
        lambda p: p["operations"][0]["params"].update(location_m=[0, 0, float("nan")]),
        lambda p: p["operations"][0]["params"].update(location_m=[0, 0, float("inf")]),
        lambda p: p["operations"][0]["params"].update(location_m=[0, 0, 11]),
        lambda p: p["operations"].insert(
            0, {"op": "subdivide_surface", "target": "arm_left", "params": {"levels": 9}}
        ),
        lambda p: p["operations"].extend(copy.deepcopy(p["operations"]) * 4),
        lambda p: p["operations"].reverse(),
        lambda p: p["operations"][0].update(
            op="add_component", target="scene", params={"component": "rear_spoiler"}
        ),
    ],
)
def test_reject_untrusted_model_plans(mutation):
    data = plan()
    mutation(data)
    with pytest.raises(ValueError):
        parse_plan(data)


def test_validated_plan_uses_tool_engine_and_preserves_existing_revision(tmp_path):
    target = tmp_path / "proposal"
    stored = store_plan(plan(), target)
    assert stored.is_file()
    assert (target / "tool-result.json").is_file()
    with pytest.raises(FileExistsError):
        store_plan(plan(), target)


def test_rejected_plan_never_written(tmp_path):
    data = plan()
    data["operations"][0]["params"]["python"] = "not allowed"
    target = tmp_path / "rejected"
    with pytest.raises(ValueError):
        store_plan(data, target)
    assert not (target / "plan.json").exists()


def test_targeted_correction_cannot_expand_its_permission():
    from apex.game_graphics.studio.contract import validate_correction_scope

    data = plan()
    assert validate_correction_scope(data, "arm_left") == data
    data["operations"].insert(
        1, {"op": "set_location", "target": "arm_right", "params": {"location_m": [0, 0, 0.12]}}
    )
    with pytest.raises(ValueError):
        validate_correction_scope(data, "arm_left")


def test_visual_progress_stops_if_one_view_regresses():
    from apex.game_graphics.studio.benchmark import compare_studio_defects

    before = [
        {"id": "alignment", "object": "arm_left", "view": v, "distance_m": 0.12} for v in ("front", "rear")
    ]
    after = [{**before[0], "distance_m": 0.05}, {**before[1], "distance_m": 0.13}]
    assert compare_studio_defects(before, after) == "regression_detected"
    assert compare_studio_defects(before, before) == "no_improvement"
    assert compare_studio_defects(before, []) is None


@pytest.mark.parametrize(
    "path", [r"C:\GameProject\Test", r"c:\gameproject\Test", "/mnt/c/GameProject/Test"]
)
def test_personal_project_is_never_a_benchmark_destination(path):
    from pathlib import Path

    from apex.game_graphics.studio.execution import refuse_personal_project

    with pytest.raises(ValueError):
        refuse_personal_project(Path(path))


def test_displacement_cannot_masquerade_as_absolute_correction():
    from apex.game_graphics.studio.contract import validate_correction_scope

    with pytest.raises(ValueError):
        validate_correction_scope(plan(), "arm_left", [0, 0, 0])


def test_subpixel_sampling_jitter_is_not_visual_progress():
    from apex.game_graphics.studio.benchmark import compare_studio_defects

    old = [
        {
            "id": "paired_component_alignment",
            "object": "arm_left",
            "view": "front",
            "distance_m": 0.12,
            "measured_y_delta_pixels": 48.84,
        }
    ]
    new = [{**old[0], "distance_m": 0.1199, "measured_y_delta_pixels": 48.79}]
    assert compare_studio_defects(old, new) == "no_improvement"
