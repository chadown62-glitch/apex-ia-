"""UE-native objective oracle; calibrated aesthetics and game FPS stay unknown until measured."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageStat

from apex.game_graphics.models import file_sha256
from apex.game_graphics.ue.contract import SHAPES, SceneCriteria, ScenePlan
from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task


def compare_reference_views(root: Path, reference_manifest: Path) -> dict[str, Any]:
    """Raw comparisons are advisory. No metric is silently promoted to calibrated visual approval."""
    execution = root / "inspect-execution.json"
    if not execution.is_file():
        return {"status": "blocked", "reason": "no_actual_native_inspection", "calibrated": False}
    receipt = json.loads(execution.read_text())
    if receipt.get("status") != "success" or receipt.get("facts", {}).get("tool") != "real_unreal_editor":
        return {"status": "blocked", "reason": "native_capture_not_qualified", "calibrated": False}
    observation = json.loads((root / "inspect-observation.json").read_text())
    captures = {c["view"]: c for c in observation.get("captures", [])}
    references = json.loads(reference_manifest.read_text())
    if not isinstance(references, list) or not 1 <= len(references) <= 4:
        raise ValueError("invalid_reference_view_manifest")
    rows = []
    for reference in references:
        if set(reference) != {"view", "path", "sha256"} or reference["view"] not in captures:
            raise ValueError("unmatched_reference_view")
        path = Path(reference["path"])
        native = Path(captures[reference["view"]]["path"])
        if (
            native.resolve().parent != (root / "captures").resolve()
            or file_sha256(native) != captures[reference["view"]]["sha256"]
            or file_sha256(path) != reference["sha256"]
            or path.stat().st_size > 16_000_000
        ):
            raise ValueError("unverified_reference_or_native_capture")
        with Image.open(native) as actual, Image.open(path) as expected:
            if expected.width * expected.height > 16_000_000:
                raise ValueError("reference_pixel_budget_exceeded")
            # In-memory analysis only; original captures/references are never rewritten.
            a = np.asarray(actual.convert("RGB"), dtype=np.float32)
            b = np.asarray(expected.convert("RGB").resize(actual.size), dtype=np.float32)
            rows.append(
                {
                    "view": reference["view"],
                    "pixel_rmse_0_255": float(np.sqrt(np.mean((a - b) ** 2))),
                    "native_sha256": captures[reference["view"]]["sha256"],
                    "reference_sha256": reference["sha256"],
                }
            )
    result = {
        "status": "advisory_comparison",
        "comparisons": rows,
        "calibrated": False,
        "used_for_acceptance": False,
        "limitations": "view correspondence, exposure and semantic quality require calibration",
    }
    (root / "reference-comparison.json").write_text(json.dumps(result, indent=2))
    return result


def compare_defects(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str | None:
    def key(item: dict[str, Any]) -> str:
        return str(item["id"]) + ":" + str(item.get("actor", ""))

    old = {key(d): d for d in before}
    current = {key(d): d for d in after}
    if set(current) - set(old):
        return "regression_detected"
    if set(current) == set(old) and not any(
        d.get("error", 0) < old[k].get("error", 0) - 1e-6 for k, d in current.items()
    ):
        return "no_improvement"
    return None


class UnrealGraphicsOracle(OracleProvider):
    def __init__(
        self,
        root: Path,
        plan: ScenePlan,
        criteria: SceneCriteria,
        build: dict[str, Any],
        inspection: dict[str, Any],
        *,
        build_execution: SandboxResult | None = None,
    ) -> None:
        self.root, self.plan, self.criteria = root, plan, criteria
        self.build, self.inspection, self.build_execution = build, inspection, build_execution

    def judge(self, *, sandbox_result: SandboxResult | None, task: Task) -> OracleResult:
        defects: list[dict[str, Any]] = []
        unavailable: list[str] = []
        if (
            sandbox_result is None
            or not self.build_execution
            or self.build_execution.status is not ExecutionStatus.SUCCESS
            or sandbox_result.status is not ExecutionStatus.SUCCESS
            or self.build.get("status") != "created"
            or self.inspection.get("status") != "inspected"
        ):
            return OracleResult(OracleVerdict.UNKNOWN, "Actual UE build/re-open/render did not complete")
        for result in (self.build_execution, sandbox_result):
            if (
                result.facts.get("tool") != "real_unreal_editor"
                or result.facts.get("model_code_executed") is not False
            ):
                return OracleResult(OracleVerdict.UNKNOWN, "Native editor execution evidence unavailable")
            if result.facts.get("critical_engine_errors"):
                defects.append(
                    {
                        "id": "engine_error_log",
                        "origin": "unreal",
                        "messages": result.facts["critical_engine_errors"],
                        "correction": "resolve the actual editor/Python/material/loader errors",
                    }
                )
        expected_hash = file_sha256(self.root / "project/Requests/scene-plan.json")
        for observation in (self.build, self.inspection):
            if (
                observation.get("plan_sha256") != expected_hash
                or observation.get("scene_id") != self.plan.scene_id
                or observation.get("revision") != self.plan.revision
                or observation.get("map_path") != self.plan.map_path
                or not observation.get("engine_version")
            ):
                return OracleResult(OracleVerdict.UNKNOWN, "Unbound scene/version inspection")
        objects = self.inspection.get("actors", {})
        if set(objects) != {a.id for a in self.plan.actors}:
            defects.append(
                {
                    "id": "actor_set",
                    "origin": "unreal",
                    "correction": "restore the agreed graphic actor IDs",
                }
            )
        materials = {m.id: m for m in self.plan.materials}
        triangles = 0
        for spec in self.plan.actors:
            obj = objects.get(spec.id)
            if not obj:
                continue
            if obj.get("owner") != "graphics" or spec.id in self.plan.protected_engineer_ids:
                defects.append(
                    {
                        "id": "ownership",
                        "actor": spec.id,
                        "correction": "refuse mutation of Engineer-owned objects",
                    }
                )
                continue
            distance = math.dist(obj["location_cm"], spec.location_cm)
            rotation = max(
                abs((a - b + 180) % 360 - 180)
                for a, b in zip(obj["rotation_deg"], spec.rotation_deg, strict=True)
            )
            if (
                not math.isfinite(distance)
                or distance > self.criteria.position_tolerance_cm
                or rotation > 0.1
            ):
                defects.append(
                    {
                        "id": "pose",
                        "actor": spec.id,
                        "origin": "unreal",
                        "error": distance + rotation,
                        "correction": "set the agreed native actor transform",
                    }
                )
            if spec.kind == "mesh":
                if spec.shape is None or spec.dimensions_cm is None or spec.material_id is None:
                    raise ValueError("invalid_typed_mesh_contract")
                if obj.get("mesh_path") != SHAPES[spec.shape]:
                    defects.append(
                        {
                            "id": "mesh_asset",
                            "actor": spec.id,
                            "origin": "unreal",
                            "correction": "load the agreed shape asset",
                        }
                    )
                size_error = max(
                    abs(a - b) for a, b in zip(obj["dimensions_cm"], spec.dimensions_cm, strict=True)
                )
                if not math.isfinite(size_error) or size_error > self.criteria.dimension_tolerance_cm:
                    defects.append(
                        {
                            "id": "size",
                            "actor": spec.id,
                            "origin": "unreal",
                            "error": size_error,
                            "correction": "rescale the native mesh from its measured bounds",
                        }
                    )
                mat = materials[spec.material_id]
                color_error = math.dist(obj["base_color"], mat.base_color)
                material_error = (
                    color_error
                    + abs(obj["roughness"] - mat.roughness)
                    + abs(obj["metallic"] - mat.metallic)
                )
                if (
                    obj["material_id"] != mat.id
                    or not obj.get("pbr_connections")
                    or not math.isfinite(material_error)
                    or material_error > 0.001
                ):
                    defects.append(
                        {
                            "id": "material",
                            "actor": spec.id,
                            "origin": "unreal",
                            "error": material_error,
                            "correction": "reconnect and assign the agreed material parameters",
                        }
                    )
                if obj.get("simulate_physics") or obj.get("collision_profile") != "BlockAll":
                    defects.append(
                        {
                            "id": "static_collision",
                            "actor": spec.id,
                            "origin": "unreal",
                            "correction": "restore static simple collision without gameplay simulation",
                        }
                    )
                if type(obj.get("triangles")) is not int or obj["triangles"] <= 0:
                    unavailable.append("native triangle measurement unavailable:" + spec.id)
                else:
                    triangles += obj["triangles"]
            else:
                if spec.intensity is None:
                    raise ValueError("invalid_typed_light_contract")
                intensity_error = abs(obj["intensity"] - spec.intensity)
                if not math.isfinite(intensity_error) or intensity_error > max(1, spec.intensity * 0.001):
                    defects.append(
                        {
                            "id": "light",
                            "actor": spec.id,
                            "origin": "unreal",
                            "error": intensity_error,
                            "correction": "restore the declared native light intensity",
                        }
                    )
        if triangles > self.criteria.max_triangles:
            defects.append(
                {
                    "id": "triangle_budget",
                    "origin": "unreal",
                    "error": triangles - self.criteria.max_triangles,
                    "correction": "reduce asset geometry or introduce qualified LOD/instancing",
                }
            )
        captures = self.inspection.get("captures", [])
        if (
            len(captures) != 4
            or {c["view"] for c in captures} != {0, 1, 2, 3}
            or len({c["sha256"] for c in captures}) != 4
        ):
            unavailable.append("four independent native views unavailable")
        for capture in captures:
            path = Path(capture["path"])
            if (
                path.resolve().parent != (self.root / "captures").resolve()
                or not path.is_file()
                or file_sha256(path) != capture["sha256"]
            ):
                defects.append(
                    {
                        "id": "capture_integrity",
                        "origin": "unreal",
                        "correction": "recapture the actual inspected scene",
                    }
                )
                continue
            with Image.open(path) as img:
                spread = max(ImageStat.Stat(img.convert("RGB")).stddev)
                if img.width < 400 or img.height < 300 or spread < 8:
                    defects.append(
                        {
                            "id": "blank_capture",
                            "origin": "unreal",
                            "correction": "repair exposure/camera/light and capture again",
                        }
                    )
        if self.criteria.min_fps is not None:
            unavailable.append("qualified target-hardware renderer FPS measurement unavailable")
        if self.criteria.calibrated_reference_required:
            unavailable.append("independent calibrated reference/aesthetic judgement unavailable")
        if self.criteria.texture_maps_required:
            unavailable.append("qualified PBR texture maps unavailable")
        detail = {
            "defects": defects,
            "unavailable": unavailable,
            "triangles": triangles,
            "criteria": self.criteria.__dict__,
            "scope": "Native UE geometry/material/light/capture checks. Aesthetics remain unqualified.",
            "producer_calibration_flags_accepted": False,
        }
        (self.root / "graphics-judge.json").write_text(json.dumps(detail, indent=2))
        return OracleResult(
            OracleVerdict.FAIL if defects else OracleVerdict.UNKNOWN if unavailable else OracleVerdict.PASS,
            json.dumps(detail),
        )
