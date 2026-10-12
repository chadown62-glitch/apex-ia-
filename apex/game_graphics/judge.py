"""A Blender oracle plugin for existing Tier0Judge; aesthetic confidence is never invented."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image, ImageStat

from apex.game_graphics.contract import WHEELS, QualityCriteria, VehicleSpec
from apex.game_graphics.models import file_sha256
from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task


class BlenderGeometryOracle(OracleProvider):
    def __init__(
        self,
        *,
        root: Path,
        spec: VehicleSpec,
        criteria: QualityCriteria,
        blend_report: dict[str, Any],
        glb_report: dict[str, Any],
        lod_report: dict[str, Any] | None = None,
    ) -> None:
        self.root, self.spec, self.criteria = root, spec, criteria
        self.blend, self.glb = blend_report, glb_report
        self.lod = lod_report

    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        defects: list[dict[str, Any]] = []
        unavailable: list[str] = []
        facts: dict[str, Any] = {}
        try:
            if sandbox_result.status is not ExecutionStatus.SUCCESS or not self.blend or not self.glb:
                return OracleResult(OracleVerdict.UNKNOWN, "Blender inspection/import did not complete")
            manifest = json.loads((self.root / "manifest.json").read_text())
            schema = Path(__file__).resolve().parents[2] / "docs/game_graphics/vehicle-asset-v1.schema.json"
            errors = list(Draft202012Validator(json.loads(schema.read_text())).iter_errors(manifest))
            if errors:
                return OracleResult(OracleVerdict.FAIL, "VehicleAssetContract schema invalid")
            required = {
                "vehicle.blend",
                "vehicle.glb",
                "spec.json",
                "vehicle-asset-v1.schema.json",
                "front_three_quarter.png",
                "rear_three_quarter.png",
                "side.png",
                "front.png",
            }
            if not required.issubset({f["path"] for f in manifest["files"]}):
                defects.append({"id": "missing_artifact", "correction": "supply the complete asset bundle"})
            if VehicleSpec.from_dict(manifest["spec"]) != self.spec:
                defects.append(
                    {"id": "spec_mismatch", "correction": "preserve the requested specification"}
                )
            if len(manifest["wheels"]) != 4 or {w["object"] for w in manifest["wheels"]} != set(WHEELS):
                defects.append({"id": "wheel_contract", "correction": "provide four distinct named wheels"})
            if VehicleSpec.from_dict(json.loads((self.root / "spec.json").read_text())) != self.spec:
                defects.append(
                    {"id": "spec_file_mismatch", "correction": "preserve the input specification"}
                )
            for f in manifest["files"]:
                path = self.root / f["path"]
                if (
                    path.parent.resolve() != self.root.resolve()
                    or not path.is_file()
                    or file_sha256(path) != f["sha256"]
                ):
                    defects.append(
                        {
                            "id": "artifact_integrity",
                            "file": f["path"],
                            "correction": "restore verified artifact",
                        }
                    )
            if manifest["units"] != "m" or manifest["schema_version"] != 1:
                defects.append({"id": "units_contract", "correction": "export version 1 in metres"})
            expected = (self.spec.length_m, self.spec.width_m, self.spec.height_m)
            wheel_centers = {
                name: [
                    front * self.spec.wheelbase_m / 2,
                    side * (self.spec.width_m / 2 - self.spec.wheel_width_m / 2),
                    self.spec.wheel_radius_m,
                ]
                for name, front, side in [
                    (WHEELS[0], 1, -1),
                    (WHEELS[1], 1, 1),
                    (WHEELS[2], -1, -1),
                    (WHEELS[3], -1, 1),
                ]
            }
            for wheel in manifest["wheels"]:
                if math.dist(wheel["center_m"], wheel_centers[wheel["object"]]) > 1e-6:
                    defects.append(
                        {
                            "id": "wheel_contract_center",
                            "object": wheel["object"],
                            "correction": "retain centers from the requested specification",
                        }
                    )
            reports = [("blend", self.blend), ("glb", self.glb)]
            if self.lod:
                reports.append(("lod1", self.lod))
            for kind, report in reports:
                if len(report.get("dimensions", [])) != 3 or not all(
                    math.isfinite(v) for v in report["dimensions"]
                ):
                    defects.append({"id": "empty_geometry", "format": kind})
                    continue
                if any(
                    abs(a - b) > self.criteria.dimension_tolerance_m
                    for a, b in zip(report["dimensions"], expected, strict=True)
                ):
                    defects.append(
                        {
                            "id": "dimensions",
                            "format": kind,
                            "measured": report["dimensions"],
                            "expected": expected,
                            "correction": "restore requested metre dimensions",
                        }
                    )
                if report["triangles"] > self.criteria.max_triangles:
                    defects.append(
                        {
                            "id": "triangle_budget",
                            "measured": report["triangles"],
                            "limit": self.criteria.max_triangles,
                            "correction": "reduce mesh complexity",
                        }
                    )
                for name, obj in report["objects"].items():
                    if (
                        obj["degenerate_faces"]
                        or obj["invalid_normals"]
                        or obj["signed_volume"] <= 0
                        or not math.isfinite(obj["signed_volume"])
                    ):
                        defects.append(
                            {
                                "id": "invalid_geometry",
                                "object": name,
                                "format": kind,
                                "degenerate_faces": obj["degenerate_faces"],
                                "invalid_normals": obj["invalid_normals"],
                                "signed_volume": obj["signed_volume"],
                                "correction": "remove zero-area faces and recalculate normals",
                            }
                        )
                    if obj["nonmanifold_edges"]:
                        defects.append(
                            {
                                "id": "nonmanifold",
                                "object": name,
                                "format": kind,
                                "count": obj["nonmanifold_edges"],
                                "correction": "repair boundary/nonmanifold edges",
                            }
                        )
                    if not obj["uv_layers"] or not obj["uv_finite"] or not obj["materials"]:
                        defects.append(
                            {
                                "id": "uv_material",
                                "object": name,
                                "format": kind,
                                "correction": "create finite UVs and assign material slots",
                            }
                        )
                for wheel in manifest["wheels"]:
                    obj = report["objects"].get(wheel["object"] + ("_LOD1" if kind == "lod1" else ""))
                    if obj is None or wheel["object"] not in WHEELS:
                        defects.append({"id": "missing_wheel", "object": wheel["object"], "format": kind})
                        continue
                    for key in ("pivot", "geometry_center"):
                        distance = math.dist(obj[key], wheel_centers[wheel["object"]])
                        if distance > self.criteria.pivot_tolerance_m:
                            defects.append(
                                {
                                    "id": "wheel_" + key,
                                    "object": wheel["object"],
                                    "format": kind,
                                    "distance_m": distance,
                                    "measured": obj[key],
                                    "expected": wheel["center_m"],
                                    "correction": "centre the wheel geometry at its specified pivot",
                                }
                            )
                    if abs(abs(obj["spin_axis_world"][1]) - 1) > 0.001:
                        defects.append(
                            {
                                "id": "wheel_orientation",
                                "object": wheel["object"],
                                "format": kind,
                                "correction": "align wheel spin to Blender Y; preserve glTF conversion",
                            }
                        )
                facts[kind] = {
                    "triangles": report["triangles"],
                    "dimensions": report["dimensions"],
                    "objects": len(report["objects"]),
                }
            view_files = [f for f in manifest["files"] if f["role"] == "view"]
            if len(view_files) < self.criteria.minimum_views or len({f["sha256"] for f in view_files}) < 4:
                defects.append(
                    {"id": "multiview_evidence", "correction": "render four distinct camera views"}
                )
            for f in view_files:
                with Image.open(self.root / f["path"]) as img:
                    spread = max(ImageStat.Stat(img.convert("RGB")).stddev)
                    if img.width < 400 or img.height < 300 or spread < 8:
                        defects.append(
                            {
                                "id": "invalid_render",
                                "file": f["path"],
                                "pixel_stddev": spread,
                                "correction": "render the actual scene; a blank image is not evidence",
                            }
                        )
            if self.lod and self.lod["triangles"] >= self.glb["triangles"]:
                defects.append({"id": "lod_reduction", "correction": "produce an actually smaller LOD"})
            if len(manifest["lods"]) < self.criteria.required_lods or (
                self.criteria.required_lods and not self.lod
            ):
                unavailable.append("required LODs unavailable")
            if self.criteria.required_texture_size and not manifest["texture_maps"]:
                unavailable.append("required PBR texture maps unavailable")
            if self.criteria.require_calibrated_visual_reference:
                unavailable.append("calibrated visual/reference assessment unavailable")
            report = {
                "defects": defects,
                "unavailable": unavailable,
                "facts": facts,
                "criteria": self.criteria.__dict__,
                "limits": [
                    "objective geometry and pixel checks do not certify aesthetic quality",
                    "engine import/performance not measured",
                ],
            }
            (self.root / "judge-report.json").write_text(json.dumps(report, indent=2))
            verdict = (
                OracleVerdict.FAIL
                if defects
                else OracleVerdict.UNKNOWN
                if unavailable
                else OracleVerdict.PASS
            )
            return OracleResult(verdict, json.dumps(report))
        except Exception as exc:
            return OracleResult(OracleVerdict.UNKNOWN, "Inspection unavailable: " + type(exc).__name__)
