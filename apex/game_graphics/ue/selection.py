"""Select by task/destination, required capabilities and evidenced cost, without silent substitutions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apex.game_graphics.ue.contract import LEVELS


@dataclass(frozen=True)
class GraphicsTask:
    kind: str
    final_in_unreal: bool
    complex_mesh: bool = False
    level: str = "prototype"
    max_gpu_mb: int = 8192
    max_ram_mb: int = 16384

    def __post_init__(self) -> None:
        if (
            self.kind
            not in (
                "vehicle",
                "mesh",
                "uv_retopology",
                "environment",
                "lighting",
                "materials",
                "landscape",
                "pcg",
                "modeling_tools",
            )
            or self.level not in LEVELS
        ):
            raise ValueError("unsupported_graphics_task")
        if type(self.final_in_unreal) is not bool or type(self.complex_mesh) is not bool:
            raise ValueError("invalid_graphics_task_flags")
        if not 512 <= self.max_gpu_mb <= 131072 or not 2048 <= self.max_ram_mb <= 262144:
            raise ValueError("invalid_graphics_resource_budget")


def select_method(
    task: GraphicsTask, capabilities: dict[str, Any], *, costs: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    mesh_work = task.kind in ("vehicle", "mesh", "uv_retopology") or task.complex_mesh
    if mesh_work and task.final_in_unreal:
        method, stages = "hybrid", ["blender", "unreal"]
        reason = "complex mesh/UV work followed by required Unreal integration"
    elif mesh_work:
        method, stages = "blender", ["blender"]
        reason = "mesh topology/UV work matches Blender tools"
    else:
        method, stages = "unreal", ["unreal"]
        reason = "level/material/light/environment work belongs in the target editor"
    candidates: list[dict[str, Any]] = []
    # Cost can choose an alternative only if capabilities and destination constraints match.
    methods = [method]
    if task.kind == "modeling_tools" and not task.complex_mesh:
        methods = ["unreal", "hybrid"] if task.final_in_unreal else ["unreal", "blender"]
    blocked = []
    for candidate in methods:
        candidate_stages = ["blender", "unreal"] if candidate == "hybrid" else [candidate]
        available = all(capabilities.get(s, {}).get("available") for s in candidate_stages)
        feature = capabilities.get("features", {}).get(task.kind, {})
        qualified = feature.get(candidate, False)
        cost = (costs or {}).get(candidate, {})
        measured = (
            cost.get("status") == "measured"
            and cost.get("task_kind") == task.kind
            and cost.get("level") == task.level
        )
        within = not measured or (
            cost.get("peak_gpu_mb", float("inf")) <= task.max_gpu_mb
            and cost.get("peak_ram_mb", float("inf")) <= task.max_ram_mb
        )
        candidates.append(
            {
                "method": candidate,
                "stages": candidate_stages,
                "available": available,
                "operation_qualified": bool(qualified),
                "cost": cost if measured else {"status": "unknown"},
                "within_budget": within,
            }
        )
    eligible = [c for c in candidates if c["available"] and c["operation_qualified"] and c["within_budget"]]
    if eligible:
        method, stages = eligible[0]["method"], eligible[0]["stages"]
    measured_eligible = [c for c in eligible if c["cost"].get("status") == "measured"]
    if len(measured_eligible) == len(eligible) and len(eligible) > 1:
        chosen = min(measured_eligible, key=lambda c: c["cost"].get("elapsed_s", float("inf")))
        method, stages = chosen["method"], chosen["stages"]
        reason += "; compatible alternatives compared using measurements for this task/quality"
    if not eligible:
        blocked = [
            "required_tool_missing"
            if not any(c["available"] for c in candidates)
            else "operation_not_qualified_or_resource_budget_exceeded"
        ]
    return {
        "method": method,
        "stages": stages,
        "status": "selected" if eligible else "blocked",
        "reason": reason,
        "candidates": candidates,
        "blockers": blocked,
        "level": task.level,
        "silent_fallback_allowed": False,
        "quality_before_speed": True,
        "cost_limitations": "Costs and GPU/RAM fit require measurements for this task.",
    }
