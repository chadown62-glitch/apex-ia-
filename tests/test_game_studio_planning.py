"""The model may propose work, but only a bounded, feasible DAG can execute."""

from __future__ import annotations

import pytest

from apex.game_studio.planning import validate_plan

CAP = {
    name: {"status": "available_unqualified"}
    for name in ("vehicle_blender", "building_blender", "chair_blender")
}
REQ = {
    "description": "Build a car and a building in Blender.",
    "tool": "blender",
    "style": "realiste",
    "max_time_s": 1800,
    "reference_count": 0,
}


def step(key, kind, deps=()):
    return {
        "key": key,
        "kind": kind,
        "description": "Create a distinct asset or assemble it.",
        "depends_on": list(deps),
    }


def test_model_plan_is_topologically_validated_and_capability_labeled():
    raw = {
        "steps": [
            step("car", "vehicle"),
            step("shop", "building"),
            step("scene", "assemble", ("car", "shop")),
        ],
        "unmet_requirements": [],
    }
    plan = validate_plan(raw, request=REQ, capabilities=CAP)
    assert [s["kind"] for s in plan["steps"]] == ["vehicle", "building", "assemble"]
    assert plan["fully_feasible"]


@pytest.mark.parametrize(
    "steps",
    [
        [step("car", "vehicle", ("car",))],
        [step("car", "vehicle"), step("scene", "assemble", ("car",)), step("shop", "building")],
        [step("car", "vehicle"), step("engine", "code")],
        [step("car", "vehicle"), step("ue", "unreal_handoff", ("car",))],
    ],
)
def test_unsafe_or_hallucinated_edges_and_tools_cannot_start(steps):
    with pytest.raises(ValueError):
        validate_plan({"steps": steps, "unmet_requirements": []}, request=REQ, capabilities=CAP)


def test_unavailable_asset_blocks_assembly_without_fallback():
    raw = {
        "steps": [
            step("car", "vehicle"),
            step("shop", "building"),
            step("scene", "assemble", ("car", "shop")),
        ],
        "unmet_requirements": [],
    }
    cap = {**CAP, "building_blender": {"status": "blocked_dependency"}}
    plan = validate_plan(raw, request=REQ, capabilities=cap)
    assert [s["status"] for s in plan["steps"]] == [
        "available_unqualified",
        "blocked_dependency",
        "blocked_dependency",
    ]


def test_unreal_handoff_from_model_is_labeled_blocked_and_never_executed():
    raw = {
        "steps": [
            {**step("libraryFacade", "building"), "description": "Create a library facade"},
            step("unrealHandoffLibrary", "unsupported", ("libraryFacade",)),
        ],
        "unmet_requirements": [],
    }
    request = {**REQ, "description": "Create a library facade and import it in Unreal Engine 5."}
    plan = validate_plan(raw, request=request, capabilities=CAP)
    assert plan["steps"][1]["kind"] == "unreal_handoff"
    assert plan["steps"][1]["status"] == "blocked_dependency"


def test_requested_unreal_cannot_be_silently_dropped():
    request = {**REQ, "description": "Create a car for Unreal Engine 5."}
    with pytest.raises(ValueError, match="unreal_handoff_required"):
        validate_plan(
            {"steps": [step("car", "vehicle")], "unmet_requirements": []}, request=request, capabilities=CAP
        )


def test_car_body_and_wheels_cannot_be_misplanned_as_two_vehicles():
    raw = {
        "steps": [
            step("car_body", "vehicle"),
            step("wheels", "vehicle"),
            step("assemble_car", "assemble", ("car_body", "wheels")),
        ],
        "unmet_requirements": [],
    }
    request = {**REQ, "description": "Crée une voiture sportive avec quatre roues séparées."}
    with pytest.raises(ValueError, match="vehicle_components_must_remain_inside_one_vehicle_asset"):
        validate_plan(raw, request=request, capabilities=CAP)
    two_cars = {**request, "description": "Crée deux voitures sportives distinctes."}
    two_car_plan = {
        "steps": [
            step("car_one", "vehicle"),
            step("car_two", "vehicle"),
            step("scene", "assemble", ("car_one", "car_two")),
        ],
        "unmet_requirements": [],
    }
    assert validate_plan(two_car_plan, request=two_cars, capabilities=CAP)["fully_feasible"]


def test_coverage_defect_is_returned_to_model_and_retry_is_bounded(tmp_path, monkeypatch):
    import json
    from dataclasses import dataclass
    from types import SimpleNamespace

    from apex.game_studio import planning

    @dataclass
    class Result:
        text: str

    bad = {"steps": [step("seat", "chair")], "unmet_requirements": []}
    good = {
        "steps": [
            step("seat1", "chair"),
            step("seat2", "chair"),
            step("scene", "assemble", ("seat1", "seat2")),
        ],
        "unmet_requirements": [],
    }
    prompts = []

    def generate(req):
        prompts.append(req.prompt)
        return Result(json.dumps(bad if len(prompts) == 1 else good))

    monkeypatch.setattr(
        planning, "get_default_provider", lambda endpoint: SimpleNamespace(generate=generate)
    )
    endpoint = SimpleNamespace(runtime="openai_compatible", url="http://127.0.0.1:9876")
    req = {**REQ, "description": "Crée deux fauteuils dans une scène.", "automatic_corrections": True}
    trace = tmp_path / "trace.json"
    assert len(planning.generate_plan(endpoint, request=req, capabilities=CAP, output=trace)["steps"]) == 3
    assert len(prompts) == 2 and "quantity_mismatch" in prompts[1]
    assert "requirement_coverage_failed" in json.loads(trace.read_text())["attempts"][0]["rejection"]
    prompts.clear()
    with pytest.raises(ValueError, match="requirement_coverage_failed"):
        planning.generate_plan(
            endpoint,
            request={**req, "automatic_corrections": False},
            capabilities=CAP,
            output=tmp_path / "disabled.json",
        )
    assert len(prompts) == 1
