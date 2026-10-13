"""Specialized Blender evidence oracle plugged into APEX's existing Tier0Judge.

A pass concerns the declared, measured technical rules. It never certifies
beauty, resemblance to an external reference, or Unreal compatibility.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageStat

from apex.game_graphics.contract import QualityCriteria
from apex.game_graphics.models import file_sha256
from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task

VIEWS = ("front_three_quarter", "rear_three_quarter", "side", "front")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
REQUIRED_FILES = {
    "asset.blend",
    "asset.glb",
    "hierarchy.json",
    "blend-inspection.json",
    "glb-inspection.json",
    "execution.json",
    "render-settings.json",
    "operation-trace.json",
    *(view + ".png" for view in VIEWS),
}


def bundle_path(root: Path, relative: str) -> Path:
    """Resolve a local artifact; never follow an escape from the bundle."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid_artifact_path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("artifact_path_escape")
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError("artifact_path_escape")
    return resolved


def verified_manifest(root: Path) -> dict[str, Any]:
    """Integrity only: SHA attestations do not themselves prove artistic quality."""
    manifest = json.loads((root / "manifest.json").read_text())
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("missing_artifact_manifest")
    seen: set[str] = set()
    for entry in files:
        relative = entry.get("path")
        if relative in seen or relative in ("manifest.json", "judge-report.json", "verdict.json"):
            raise ValueError("duplicate_or_circular_artifact")
        digest = entry.get("sha256", "")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("invalid_artifact_digest")
        path = bundle_path(root, relative)
        if not path.is_file() or file_sha256(path) != digest:
            raise ValueError("artifact_integrity:" + relative)
        seen.add(relative)
    missing = REQUIRED_FILES - seen
    if missing:
        raise ValueError("missing_artifacts:" + ",".join(sorted(missing)))
    return manifest


def _finite_vector(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(type(item) in (int, float) and math.isfinite(item) for item in value)
    )


class StudioGeometryOracle(OracleProvider):
    def __init__(
        self,
        *,
        root: Path,
        family: str,
        criteria: QualityCriteria,
        benchmark: dict[str, Any] | Path,
        blend_report: dict[str, Any] | None = None,
        glb_report: dict[str, Any] | None = None,
        visual: dict[str, Any] | None = None,
    ) -> None:
        self.root, self.family, self.criteria = root, family, criteria
        self.benchmark = json.loads(benchmark.read_text()) if isinstance(benchmark, Path) else benchmark
        self.case = next(case for case in self.benchmark["cases"] if case["family"] == family)
        self.reports = {"blend": blend_report, "glb": glb_report}
        self.visual = visual

    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        report: dict[str, Any] = {
            "family": self.family,
            "case_id": self.case["id"],
            "criteria": asdict(self.criteria),
            "benchmark_version": self.benchmark["version"],
            "scope": "measured_geometry_export_and_render_rules",
            "defects": [],
            "unavailable": [],
            "facts": {},
            "advisory": [],
            "aesthetic_certification": False,
            "limits": [
                "Geometry and image statistics do not certify aesthetic quality or reference fidelity.",
                "Isolated component ID passes measure alignment, not visibility in the beauty render.",
                "Unreal import and runtime performance have not been measured by this oracle.",
            ],
        }
        defects, unavailable, facts = report["defects"], report["unavailable"], report["facts"]

        def defect(identifier: str, correction: str, **detail: Any) -> None:
            defects.append(
                {
                    "id": identifier,
                    "object": detail.pop("object", "scene"),
                    "reference": self.case["reference"],
                    "correction": correction,
                    "expected_improvement": correction,
                    "confidence": 1.0,
                    "confidence_scope": "deterministic measured rule violation, not aesthetic judgment",
                    **detail,
                }
            )

        def finish() -> OracleResult:
            verdict = (
                OracleVerdict.FAIL
                if defects
                else OracleVerdict.UNKNOWN
                if unavailable
                else OracleVerdict.PASS
            )
            report["verdict"] = verdict.value
            serialized = json.dumps(report, indent=2, allow_nan=False)
            (self.root / "judge-report.json").write_text(serialized)
            return OracleResult(verdict, serialized)

        try:
            if sandbox_result.status is not ExecutionStatus.SUCCESS:
                unavailable.append("Native Blender execution or independent reopening did not complete.")
                return finish()
            try:
                manifest = verified_manifest(self.root)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                defect(
                    "artifact_integrity",
                    "Restore the complete unmodified evidence bundle.",
                    observed=str(exc),
                    verification="Every manifest artifact exists and has its attested SHA256.",
                )
                return finish()
            if manifest.get("schema_version") != 1 or manifest.get("units") != "m":
                defect(
                    "units_contract",
                    "Export schema version 1 in metres.",
                    verification="schema_version=1 and units=m",
                )
            if manifest.get("family") != self.family or manifest.get("case_id") != self.case["id"]:
                defect(
                    "family_contract",
                    "Restore the requested asset family and frozen benchmark case.",
                    verification="Manifest family and case match the requested task.",
                )
            files = {entry["path"]: entry for entry in manifest["files"]}
            report["manifest_sha256"] = file_sha256(self.root / "manifest.json")
            settings = json.loads((self.root / "render-settings.json").read_text())
            expected_render = self.benchmark["render"]
            measured_render = {
                key: settings.get(key) for key in ("engine", "device", "samples", "seed", "denoising")
            }
            for key, measured in measured_render.items():
                expected = expected_render[key]
                # Equality alone accepts bool as 0/1; the native setting must have its exact type.
                if type(measured) is not type(expected) or measured != expected:
                    defect(
                        "render_settings",
                        "Use the frozen render settings.",
                        setting=key,
                        measured=measured,
                        expected=expected,
                        verification="Native render settings equal the declared benchmark settings.",
                    )
            resolution = settings.get("resolution")
            if (
                not isinstance(resolution, list)
                or len(resolution) != 2
                or any(type(value) is not int for value in resolution)
                or resolution != [expected_render["width"], expected_render["height"]]
            ):
                defect(
                    "render_resolution",
                    "Use the frozen native render resolution.",
                    measured=resolution,
                    verification="Resolution is the benchmark width and height.",
                )
            poses: set[str] = set()
            cameras = settings.get("cameras", {})
            for view in VIEWS:
                camera = cameras.get("camera_" + view, {})
                matrix, scale = camera.get("matrix"), camera.get("scale")
                valid_matrix = (
                    isinstance(matrix, list)
                    and len(matrix) == 4
                    and all(isinstance(row, list) and len(row) == 4 for row in matrix)
                    and all(
                        type(value) in (int, float) and math.isfinite(value)
                        for row in matrix
                        for value in row
                    )
                )
                valid_scale = type(scale) in (int, float) and math.isfinite(scale) and scale > 0
                if not valid_matrix or not valid_scale:
                    defect(
                        "camera_contract",
                        "Save a finite camera transform and positive projection scale.",
                        object="camera_" + view,
                        verification="Camera has a finite 4x4 matrix and positive orthographic scale.",
                    )
                else:
                    poses.add(json.dumps([[round(value, 7) for value in row] for row in matrix]))
            if len(poses) < len(VIEWS):
                defect(
                    "distinct_camera_views",
                    "Use four distinct camera transforms.",
                    measured=len(poses),
                    expected=len(VIEWS),
                    verification="The four named beauty cameras have distinct finite native transforms.",
                )
            facts["render_settings"] = measured_render
            facts["distinct_camera_transforms"] = len(poses)
            inspections: dict[str, dict[str, Any]] = {}
            for kind in ("blend", "glb"):
                artifact = self.root / ("asset." + kind)
                inspection = json.loads((self.root / (kind + "-inspection.json")).read_text())
                supplied = self.reports[kind]
                if supplied is not None and supplied != inspection:
                    defect(
                        "inspection_mismatch",
                        "Judge the attested independent inspection file.",
                        format=kind,
                        verification="Supplied inspection equals the persisted, hashed inspection.",
                    )
                    continue
                inspections[kind] = inspection
                if inspection.get("independent_reopen") is not True:
                    defect(
                        "independent_reopen",
                        "Reopen the saved artifact in a separate Blender process.",
                        format=kind,
                        verification="The native inspection records independent reopening.",
                    )
                if (
                    inspection.get("source_sha256") != file_sha256(artifact)
                    or inspection.get("source_format") != "." + kind
                ):
                    defect(
                        "inspection_source",
                        "Reopen this exact saved asset in a fresh Blender process.",
                        format=kind,
                        verification="Inspection source SHA256 and format match the saved artifact.",
                    )
                dimensions = inspection.get("dimensions")
                if not _finite_vector(dimensions):
                    defect(
                        "empty_geometry",
                        "Create a finite, nonempty mesh asset.",
                        format=kind,
                        verification="Independent world-space dimensions contain three finite values.",
                    )
                    continue
                if any(
                    not low <= value <= high
                    for value, low, high in zip(
                        dimensions,
                        self.case["bounds_min_m"],
                        self.case["bounds_max_m"],
                        strict=True,
                    )
                ):
                    defect(
                        "dimensions",
                        "Restore the frozen family dimension bounds.",
                        format=kind,
                        measured=dimensions,
                        minimum=self.case["bounds_min_m"],
                        maximum=self.case["bounds_max_m"],
                        verification="All three measured dimensions lie inside the frozen metre bounds.",
                    )
                if inspection.get("scene_unit_scale") != 1:
                    defect(
                        "scene_units",
                        "Use a metre-scale Blender scene.",
                        format=kind,
                        measured=inspection.get("scene_unit_scale"),
                        verification="scene_unit_scale equals 1.",
                    )
                objects = inspection.get("objects", {})
                if not isinstance(objects, dict) or not objects:
                    defect(
                        "empty_geometry",
                        "Create the requested mesh components.",
                        format=kind,
                        verification="The independently reopened asset contains mesh objects.",
                    )
                    continue
                for name in self.case["required_objects"]:
                    if name not in objects:
                        defect(
                            "missing_component",
                            "Create and export the named component.",
                            object=name,
                            format=kind,
                            verification="Named mesh occurs in both reopened formats.",
                        )
                triangles = inspection.get("triangles")
                if type(triangles) is not int or triangles <= 0 or triangles > self.criteria.max_triangles:
                    defect(
                        "triangle_budget",
                        "Reduce topology while retaining the required components.",
                        format=kind,
                        measured=triangles,
                        limit=self.criteria.max_triangles,
                        verification="Positive triangle count is within the selected quality budget.",
                    )
                materials: set[str] = set()
                for name, obj in objects.items():
                    materials.update(obj.get("materials", []))
                    volume = obj.get("signed_volume")
                    if (
                        obj.get("degenerate_faces") != 0
                        or obj.get("invalid_normals") != 0
                        or type(volume) not in (int, float)
                        or not math.isfinite(volume)
                        or volume <= 0
                    ):
                        defect(
                            "invalid_geometry",
                            "Remove zero-area faces and orient a closed positive-volume surface.",
                            object=name,
                            format=kind,
                            degenerate_faces=obj.get("degenerate_faces"),
                            invalid_normals=obj.get("invalid_normals"),
                            signed_volume=volume
                            if type(volume) in (int, float) and math.isfinite(volume)
                            else str(volume),
                            verification="Zero degenerate faces/invalid normals; positive finite volume.",
                        )
                    if obj.get("nonmanifold_edges") != 0:
                        defect(
                            "nonmanifold",
                            "Repair open or nonmanifold edges locally.",
                            object=name,
                            format=kind,
                            count=obj.get("nonmanifold_edges"),
                            verification="Independent welded topology has zero nonmanifold edges.",
                        )
                    if (
                        not obj.get("uv_layers")
                        or obj.get("uv_finite") is not True
                        or not obj.get("materials")
                    ):
                        defect(
                            "uv_material",
                            "Assign a material and finite UV coordinates to the component.",
                            object=name,
                            format=kind,
                            verification="At least one material and one finite UV layer.",
                        )
                if len(materials) < self.case["minimum_materials"]:
                    defect(
                        "material_separation",
                        "Assign the required distinct functional materials.",
                        format=kind,
                        measured=len(materials),
                        minimum=self.case["minimum_materials"],
                        verification="Independently reopened material count meets the frozen minimum.",
                    )
                facts[kind] = {
                    "triangles": triangles,
                    "dimensions": dimensions,
                    "objects": len(objects),
                    "materials": len(materials),
                }
            if len(inspections) == 2:
                if set(inspections["blend"].get("objects", {})) != set(
                    inspections["glb"].get("objects", {})
                ):
                    defect(
                        "export_object_set",
                        "Preserve all asset mesh identities through GLB export.",
                        verification="Blend and GLB inspections contain the same mesh object names.",
                    )
                left, right = (
                    inspections["blend"].get("dimensions"),
                    inspections["glb"].get("dimensions"),
                )
                if (
                    _finite_vector(left)
                    and _finite_vector(right)
                    and any(
                        abs(a - b) > self.criteria.dimension_tolerance_m
                        for a, b in zip(left, right, strict=True)
                    )
                ):
                    defect(
                        "export_dimensions",
                        "Preserve metre dimensions through GLB export.",
                        verification="Blender/GLB axis differences stay within the dimension tolerance.",
                    )
            view_digests: list[str] = []
            image_facts: dict[str, Any] = {}
            silhouettes = {
                entry.get("view"): entry for entry in manifest["files"] if entry.get("role") == "silhouette"
            }
            for view in VIEWS:
                entry = files[view + ".png"]
                view_digests.append(entry["sha256"])
                with Image.open(self.root / entry["path"]) as image:
                    spread = max(ImageStat.Stat(image.convert("RGB")).stddev)
                    image_facts[view] = {
                        "width": image.width,
                        "height": image.height,
                        "channel_stddev": spread,
                    }
                    expected_size = (
                        self.benchmark["render"]["width"],
                        self.benchmark["render"]["height"],
                    )
                    if (
                        image.size != expected_size
                        or spread < self.benchmark["images"]["minimum_channel_stddev"]
                    ):
                        defect(
                            "invalid_render",
                            "Render the saved asset at the frozen resolution with visible detail.",
                            file=entry["path"],
                            measured=image_facts[view],
                            verification="Exact benchmark dimensions and nonblank pixel variation.",
                        )
                silhouette = silhouettes.get(view)
                if silhouette is None:
                    unavailable.append("Native subject silhouette unavailable for " + view + ".")
                    continue
                with Image.open(bundle_path(self.root, silhouette["path"])) as image:
                    mask = image.convert("RGB").convert("L")
                    histogram = mask.histogram()
                    visible = sum(histogram[128:])
                    fraction = visible / (image.width * image.height)
                    image_facts[view]["subject_fraction"] = fraction
                    # A reasonable occupied area does not exclude a cut-off subject.
                    border = (
                        list(mask.crop((0, 0, image.width, 1)).getdata())
                        + list(mask.crop((0, image.height - 1, image.width, image.height)).getdata())
                        + list(mask.crop((0, 1, 1, image.height - 1)).getdata())
                        + list(mask.crop((image.width - 1, 1, image.width, image.height - 1)).getdata())
                    )
                    edge_pixels = sum(pixel >= 128 for pixel in border)
                    image_facts[view]["subject_pixels_at_image_boundary"] = edge_pixels
                    if edge_pixels:
                        defect(
                            "frame_clipping",
                            "Expand the camera frame to keep the entire subject away from image edges.",
                            file=silhouette["path"],
                            measured=edge_pixels,
                            verification="Native subject silhouette has no occupied boundary pixels.",
                        )
                    if (
                        image.size != expected_size
                        or not self.benchmark["images"]["minimum_subject_fraction"]
                        <= fraction
                        <= self.benchmark["images"]["maximum_subject_fraction"]
                    ):
                        defect(
                            "framing",
                            "Reframe the complete asset within the required image fraction.",
                            file=silhouette["path"],
                            measured=fraction,
                            verification="Native white-subject pixel fraction is within frozen bounds.",
                        )
            if len(set(view_digests)) < self.benchmark["images"]["minimum_distinct_views"]:
                defect(
                    "multiview_evidence",
                    "Render four genuinely distinct camera views.",
                    verification="Four distinct native render digests.",
                )
            if self.criteria.minimum_views > len(VIEWS):
                unavailable.append("The selected custom minimum exceeds the four validated views.")
            facts["images"] = image_facts
            if self.family == "chair" and not self.visual:
                unavailable.append("Required paired chair alignment observations are missing.")
            if self.visual:
                persisted_visual = json.loads((self.root / "visual-alignment.json").read_text())
                if "visual-alignment.json" not in files or self.visual != persisted_visual:
                    unavailable.append("Visual observations are not bound to this bundle.")
                if self.visual.get("status") == "fail" and not self.visual.get("defects"):
                    unavailable.append("Visual failure lacks actionable evidence.")
                report["visual"] = self.visual
                if self.visual.get("required") and (
                    self.visual.get("status") not in ("pass", "fail") or self.visual.get("unavailable")
                ):
                    unavailable.append("Required visual measurement is unavailable for this scene.")
                calibration = self.visual.get("calibration", {})
                calibration_path = calibration.get("evidence_path")
                calibration_ok = False
                if calibration_path in files and files[calibration_path].get("role") == "calibration":
                    measured = json.loads(bundle_path(self.root, calibration_path).read_text())
                    threshold = self.benchmark["visual_alignment"]["max_centroid_y_difference_pixels"]
                    calibration_ok = (
                        measured.get("status") == "pass_in_observed_scope"
                        and measured.get("counts", {}).get("unknown") == 0
                        and measured.get("false_positives") == 0
                        and measured.get("false_negatives") == 0
                        and measured.get("accepted_cases", 0) >= 2
                        and measured.get("rejected_cases", 0) >= 2
                        and measured.get("threshold_pixels") == threshold
                    )
                if (
                    self.visual.get("calibrated") is True
                    and self.visual.get("scope") == "isolated_paired_component_alignment"
                    and calibration_ok
                ):
                    # This attests only the narrow fixture experiment, not general visual judgment.
                    defects.extend(self.visual.get("defects", []))
                elif self.visual.get("required"):
                    unavailable.append("Required visual metric has no verified fixture calibration.")
                else:
                    report["advisory"].append("Uncalibrated visual observations were not decisive.")
            report["advisory"].append(
                "Shader parameters and material count do not certify convincing PBR appearance."
            )
            # The small-scene qualification has no checked LODs or texture maps.
            if self.criteria.required_lods:
                unavailable.append("Required independently validated LODs unavailable.")
            if self.criteria.required_texture_size:
                unavailable.append("Required PBR texture maps and their resolution are unqualified.")
            if self.criteria.require_calibrated_visual_reference:
                unavailable.append(
                    "General aesthetic judgment is uncalibrated; alignment tests are insufficient."
                )
            return finish()
        except Exception as exc:
            unavailable.append("Inspection unavailable: " + type(exc).__name__ + ": " + str(exc))
            try:
                return finish()
            except Exception:
                return OracleResult(
                    OracleVerdict.UNKNOWN, "Unable to persist the independent inspection result."
                )


MAX_Y_DELTA_PX = 4.0
MIN_VISIBLE_PIXELS = 12
METHOD = "native_id_centroid_alignment_v1"
COLOR_RULE = "alpha>=128; channel>=200; other channels<=64; Standard/Raw emission ID"


def measure_pair(
    path: Path,
    *,
    red_object: str,
    green_object: str,
    view: str,
    reference_id: str,
    expected_sha256: str,
    pixels_per_meter: float | None = None,
) -> dict[str, Any]:
    """Pure PNG measurement; its detection becomes decisive only after calibration.

    Front/rear cameras must be orthographic, level, upright and equally framed.
    Use isolated-component diagnostic renders if the opposite view would be
    occluded; record that choice and never call it ordinary-scene visibility.
    """
    receipt: dict[str, Any] = {
        "method": METHOD,
        "path": str(path),
        "view": view,
        "reference": reference_id,
        "objects": {"red": red_object, "green": green_object},
        "criteria": {
            "max_centroid_y_difference_pixels": MAX_Y_DELTA_PX,
            "minimum_visible_pixels_per_component": MIN_VISIBLE_PIXELS,
        },
        "color_rule": COLOR_RULE,
        "measurement_status": "unknown",
        "decisive": False,
        "defects": [],
        "limits": [
            "paired component projected alignment only; no aesthetic quality score",
            "centroids depend on shape, occlusion, clipping and camera pose",
            "caller must bind PNG SHA to native renderer and frozen camera receipts",
        ],
    }
    data = path.read_bytes()
    receipt["sha256"] = hashlib.sha256(data).hexdigest()
    if receipt["sha256"] != expected_sha256:
        receipt["reason"] = "native_png_integrity_mismatch"
        return receipt
    with Image.open(path) as image:
        if image.format != "PNG" or image.width > 4096 or image.height > 4096:
            receipt["reason"] = "bounded_native_png_required"
            return receipt
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    receipt["resolution"] = [int(pixels.shape[1]), int(pixels.shape[0])]
    r, g, b, alpha = (pixels[:, :, i] for i in range(4))
    masks = {
        "red": (r >= 200) & (g <= 64) & (b <= 64) & (alpha >= 128),
        "green": (g >= 200) & (r <= 64) & (b <= 64) & (alpha >= 128),
    }
    stats: dict[str, Any] = {}
    for color, mask in masks.items():
        ys, xs = np.nonzero(mask)
        count = len(xs)
        stats[color] = {"pixels": count, "centroid_xy": None, "bbox_xyxy": None}
        if count:
            stats[color].update(
                {
                    "centroid_xy": [float(xs.mean()), float(ys.mean())],
                    "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                }
            )
    receipt["components"] = stats
    if any(item["pixels"] < MIN_VISIBLE_PIXELS for item in stats.values()):
        receipt["reason"] = "insufficient_native_id_visibility"
        receipt["feedback"] = {
            "element": [red_object, green_object],
            "observed_defect": "One or both diagnostic IDs have fewer than 12 visible pixels.",
            "expected_improvement": "Inspect occlusion, clipping and the ID material/camera receipt.",
            "confidence": {"kind": "unavailable", "calibrated": False},
            "verification": "Each component has at least 12 classified native ID pixels.",
        }
        return receipt
    signed_delta = stats["red"]["centroid_xy"][1] - stats["green"]["centroid_xy"][1]
    receipt["signed_red_minus_green_y_pixels"] = signed_delta
    receipt["absolute_y_delta_pixels"] = abs(signed_delta)
    receipt["measurement_status"] = "fail" if abs(signed_delta) > MAX_Y_DELTA_PX else "pass"
    receipt["reason"] = "projected_alignment_measured"
    if abs(signed_delta) > MAX_Y_DELTA_PX:
        defect = {
            "id": "paired_component_alignment",
            "object": red_object,
            "reference_object": green_object,
            "view": view,
            "reference": reference_id,
            "observed_defect": "Target is projected "
            + ("higher" if signed_delta < 0 else "lower")
            + " than its paired reference.",
            "measured_y_delta_pixels": abs(signed_delta),
            "expected_improvement": (
                "Restore the agreed paired component height; preserve geometry, materials and camera."
            ),
            "suggested_operation": "set_location",
            "confidence": {"kind": "uncalibrated_objective_measurement", "calibrated": False},
            "verification": {
                "views": ["front", "rear"],
                "max_y_delta_pixels": MAX_Y_DELTA_PX,
                "minimum_pixels_per_component": MIN_VISIBLE_PIXELS,
            },
        }
        if pixels_per_meter is not None:
            if (
                type(pixels_per_meter) not in (int, float)
                or not math.isfinite(pixels_per_meter)
                or pixels_per_meter <= 0
            ):
                raise ValueError("positive_finite_orthographic_pixels_per_meter_required")
            defect["estimated_target_delta_z_m"] = signed_delta / pixels_per_meter
            defect["estimate_note"] = (
                "Only valid for the frozen level orthographic camera; verify expected world position."
            )
        receipt["defects"].append(defect)
    return receipt


def calibration_report(rows: list[dict[str, Any]], *, predeclared_manifest_sha256: str) -> dict[str, Any]:
    """Evaluate fixed thresholds on actually rendered calibration records.

    Each row: id, family, expected_misaligned(bool), native_render_verified(bool),
    measurement (measure_pair result). Ground truth must come from the immutable
    calibration design, not from adjusting thresholds to predictions.
    """
    counts = {
        "true_positive": 0,
        "true_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
        "unknown": 0,
    }
    families: set[str] = set()
    evidence = []
    for row in rows:
        if type(row.get("expected_misaligned")) is not bool:
            raise ValueError("independent_boolean_calibration_ground_truth_required")
        measurement = row["measurement"]
        evidence.append(
            {
                "id": row["id"],
                "family": row["family"],
                "native_render_verified": row.get("native_render_verified") is True,
                "sha256": measurement.get("sha256"),
                "expected_misaligned": row["expected_misaligned"],
                "measurement_status": measurement["measurement_status"],
                "absolute_y_delta_pixels": measurement.get("absolute_y_delta_pixels"),
            }
        )
        if row.get("native_render_verified") is not True or measurement["measurement_status"] == "unknown":
            counts["unknown"] += 1
            continue
        families.add(row["family"])
        predicted = measurement["measurement_status"] == "fail"
        key = ("true_" if predicted == row["expected_misaligned"] else "false_") + (
            "positive" if predicted else "negative"
        )
        counts[key] += 1
    negatives = counts["true_negative"] + counts["false_positive"]
    positives = counts["true_positive"] + counts["false_negative"]
    sufficient = positives >= 2 and negatives >= 2
    passed = sufficient and not any(counts[k] for k in ("false_positive", "false_negative", "unknown"))
    return {
        "method": METHOD,
        "predeclared_manifest_sha256": predeclared_manifest_sha256,
        "threshold_pixels": MAX_Y_DELTA_PX,
        "minimum_visible_pixels": MIN_VISIBLE_PIXELS,
        "counts": counts,
        "cases": evidence,
        "families_observed": sorted(families),
        "empirical_false_positive_rate": counts["false_positive"] / negatives if negatives else None,
        "empirical_false_negative_rate": counts["false_negative"] / positives if positives else None,
        "status": "pass_in_observed_scope" if passed else "insufficient" if not sufficient else "fail",
        "decisive_scope": "same ID renderer, camera contract and paired shape class only"
        if passed
        else None,
        "limits": [
            "small calibration sample; no probability of correctness or aesthetic score",
            "unseen camera, occlusion or shape classes remain unqualified",
            "do not retune the fixed threshold on validation or heldout results",
        ],
    }
