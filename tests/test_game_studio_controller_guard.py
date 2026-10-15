"""A mixed local-model response may execute only authorized catalog operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from apex.game_graphics.studio.controller import propose_once


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
