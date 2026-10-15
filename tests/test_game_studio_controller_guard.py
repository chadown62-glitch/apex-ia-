"""A mixed local-model response may execute only authorized catalog operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apex.game_graphics.studio.contract import validate_correction_scope
from apex.game_graphics.studio.controller import propose_once, propose_plan
from apex.game_studio.service import _validate_topology_repair


def test_protected_chassis_edits_are_removed_and_recorded(tmp_path):
    raw = json.dumps({
        "family": "vehicle", "operations": [
            {"op": "inspect_scene", "target": "scene", "params": {}},
            {"op": "subdivide_surface", "target": "chassis", "params": {"levels": 1}},
            {"op": "set_material_pbr", "target": "paint", "params": {
                "base_color": [0.3, 0.2, 0.1], "roughness": 0.35, "metallic": 0.6,
            }},
            {"op": "save_revision_and_render", "target": "scene", "params": {}},
        ],
    })
    endpoint = SimpleNamespace(url="http://127.0.0.1:9999", runtime="llama_cpp")
    @dataclass
    class Result:
        text: str

    provider = SimpleNamespace(generate=lambda _request: Result(raw))
    out = tmp_path / "plan.json"
    with patch("apex.game_graphics.studio.controller.get_default_provider", return_value=provider):
        plan = propose_once(
            endpoint, family="vehicle", instruction="Improve paint safely",
            hierarchy={"chassis": {"materials": ["paint"]}}, feedback=None,
            protected_topology=("chassis",), output=out,
        )
    assert [item["op"] for item in plan["operations"]] == [
        "inspect_scene", "set_material_pbr", "save_revision_and_render",
    ]
    persisted = json.loads(out.read_text())
    assert persisted["raw_response"] == raw
    assert persisted["discarded_operations"][0]["reason"] == "topology_operation_on_protected_mesh"


def test_targeted_correction_retries_instead_of_expanding_scope(tmp_path):
    @dataclass
    class Result:
        text: str

    def response(op):
        return json.dumps({"family": "chair", "operations": [
            {"op": "inspect_scene", "target": "scene", "params": {}}, op,
            {"op": "save_revision_and_render", "target": "scene", "params": {}},
        ]})

    responses = iter([
        response({"op": "set_material_pbr", "target": "fabric", "params": {
            "base_color": [0.2, 0.3, 0.4], "roughness": 0.6, "metallic": 0,
        }}),
        response({"op": "set_location", "target": "arm_left", "params": {
            "location_m": [0.5, 0.2, 0.6],
        }}),
    ])
    provider = SimpleNamespace(generate=lambda _request: Result(next(responses)))
    endpoint = SimpleNamespace(url="http://127.0.0.1:9999", runtime="llama_cpp")
    output = tmp_path / "plan.json"
    with patch("apex.game_graphics.studio.controller.get_default_provider", return_value=provider):
        plan = propose_plan(
            endpoint, family="chair", instruction="Fix arm_left only",
            hierarchy={"arm_left": {"materials": ["fabric"], "location_m": [0.5, 0.2, 0.72]}},
            feedback=None, output=output,
            plan_validator=lambda candidate: validate_correction_scope(
                candidate, "arm_left", [0.5, 0.2, 0.6]
            ),
        )
    assert plan["operations"][1]["op"] == "set_location"
    assert json.loads(output.read_text())["accepted"] is False
    assert "correction_outside_authorized_scope" in json.loads(output.read_text())["rejection"]


def test_natural_topology_defect_cannot_be_replaced_by_a_cosmetic_edit():
    plan = {"operations": [
        {"op": "inspect_scene", "target": "scene", "params": {}},
        {"op": "set_material_pbr", "target": "warm_masonry", "params": {}},
        {"op": "save_revision_and_render", "target": "scene", "params": {}},
    ]}
    with pytest.raises(ValueError, match="correction_outside_measured_topology_defect"):
        _validate_topology_repair(plan, target="atelier_letters")
    plan["operations"][1] = {
        "op": "repair_degenerate_topology", "target": "atelier_letters", "params": {},
    }
    _validate_topology_repair(plan, target="atelier_letters")
