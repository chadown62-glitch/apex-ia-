"""Stdlib-only artistic operation catalog, also validated inside real Blender."""

from __future__ import annotations

import math
import re
from typing import Any

FAMILIES = ("vehicle", "building", "chair")
OPS = (
    "inspect_scene",
    "bevel_edges",
    "repair_degenerate_topology",
    "subdivide_surface",
    "set_location",
    "set_material_pbr",
    "add_component",
    "save_revision_and_render",
)
COMPONENTS = {
    "vehicle": ("rear_spoiler",),
    "building": ("entrance_planters",),
    "chair": ("lumbar_cushion",),
}
ID = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def number(v: Any, low: float, high: float) -> float:
    if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
        raise ValueError("nonfinite_or_out_of_range_artistic_value")
    return float(v)


def vector(v: Any, low: float, high: float) -> list[float]:
    if not isinstance(v, list) or len(v) != 3:
        raise ValueError("three_component_vector_required")
    return [number(x, low, high) for x in v]


def parse_plan(data: Any) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or set(data) != {"family", "operations"}
        or data["family"] not in FAMILIES
    ):
        raise ValueError("invalid_artistic_plan")
    if not isinstance(data["operations"], list) or not 1 <= len(data["operations"]) <= 8:
        raise ValueError("artistic_operation_budget")
    for item in data["operations"]:
        if not isinstance(item, dict) or set(item) != {"op", "target", "params"} or item["op"] not in OPS:
            raise ValueError("unknown_artistic_operation")
        if not isinstance(item["target"], str) or not ID.fullmatch(item["target"]):
            raise ValueError("invalid_object_identifier")
        p = item["params"]
        op = item["op"]
        fields = {
            "inspect_scene": set(),
            "save_revision_and_render": set(),
            "bevel_edges": {"width_m", "segments"},
            "repair_degenerate_topology": set(),
            "subdivide_surface": {"levels"},
            "set_location": {"location_m"},
            "set_material_pbr": {"base_color", "roughness", "metallic"},
            "add_component": {"component"},
        }[op]
        if not isinstance(p, dict) or set(p) != fields:
            raise ValueError("unexpected_operation_fields")
        if (
            op in ("inspect_scene", "save_revision_and_render", "add_component")
            and item["target"] != "scene"
        ):
            raise ValueError("scene_operation_target_required")
        if op == "bevel_edges":
            number(p["width_m"], 0.001, 0.15)
            if type(p["segments"]) is not int or not 1 <= p["segments"] <= 6:
                raise ValueError("bevel_budget")
        elif op == "subdivide_surface":
            if type(p["levels"]) is not int or p["levels"] not in (1, 2):
                raise ValueError("subdivision_budget")
        elif op == "set_location":
            vector(p["location_m"], -10, 10)
        elif op == "set_material_pbr":
            vector(p["base_color"], 0, 1)
            number(p["roughness"], 0.04, 1)
            number(p["metallic"], 0, 1)
        elif op == "add_component" and p["component"] not in COMPONENTS[data["family"]]:
            raise ValueError("component_outside_family_catalog")
    if (
        sum(o["op"] == "save_revision_and_render" for o in data["operations"]) != 1
        or data["operations"][-1]["op"] != "save_revision_and_render"
    ):
        raise ValueError("one_terminal_save_required")
    return data


def validate_correction_scope(plan, target, expected_location=None):
    parse_plan(plan)
    edits = [
        op for op in plan["operations"] if op["op"] not in ("inspect_scene", "save_revision_and_render")
    ]
    if len(edits) != 1 or edits[0]["op"] != "set_location" or edits[0]["target"] != target:
        raise ValueError("correction_outside_authorized_scope")
    if expected_location is not None and any(
        abs(a - b) > 1e-6 for a, b in zip(edits[0]["params"]["location_m"], expected_location, strict=True)
    ):
        raise ValueError("correction_requires_absolute_reference_location")
    return plan
