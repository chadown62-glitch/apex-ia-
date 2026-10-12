"""Strict artistic operations; no arbitrary Python, gameplay, C++ or console commands."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
LEVELS = ("prototype", "jouable", "soigne", "photorealiste")
SHAPES = {
    "cube": "/Engine/BasicShapes/Cube.Cube",
    "sphere": "/Engine/BasicShapes/Sphere.Sphere",
    "cylinder": "/Engine/BasicShapes/Cylinder.Cylinder",
}


def vector(value: Any, low: float, high: float) -> tuple[float, float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high for v in value)
    ):
        raise ValueError("invalid_scene_vector")
    return tuple(value)


def exact(data: dict[str, Any], keys: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError("unexpected_scene_fields")


@dataclass(frozen=True)
class MaterialSpec:
    id: str
    base_color: tuple[float, float, float]
    roughness: float
    metallic: float

    @classmethod
    def parse(cls, data: dict[str, Any]) -> MaterialSpec:
        exact(data, {"id", "base_color", "roughness", "metallic"})
        if not isinstance(data["id"], str) or not ID.fullmatch(data["id"]):
            raise ValueError("invalid_material_id")
        for key, low in (("roughness", 0.02), ("metallic", 0)):
            if (
                type(data[key]) not in (int, float)
                or not math.isfinite(data[key])
                or not low <= data[key] <= 1
            ):
                raise ValueError("invalid_pbr_parameter")
        return cls(data["id"], vector(data["base_color"], 0, 1), data["roughness"], data["metallic"])


@dataclass(frozen=True)
class ActorSpec:
    id: str
    owner: str
    kind: str
    shape: str | None
    material_id: str | None
    location_cm: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    dimensions_cm: tuple[float, float, float] | None
    intensity: float | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ActorSpec:
        exact(
            data,
            {
                "id",
                "owner",
                "kind",
                "shape",
                "material_id",
                "location_cm",
                "rotation_deg",
                "dimensions_cm",
                "intensity",
            },
        )
        if not isinstance(data["id"], str) or not ID.fullmatch(data["id"]) or data["owner"] != "graphics":
            raise ValueError("actor_id_or_owner_refused")
        if data["kind"] == "mesh":
            if (
                data["shape"] not in SHAPES
                or not isinstance(data["material_id"], str)
                or data["intensity"] is not None
            ):
                raise ValueError("unsupported_graphics_mesh_operation")
            dims = vector(data["dimensions_cm"], 1, 20000)
        elif data["kind"] in ("directional_light", "point_light"):
            if (
                data["shape"] is not None
                or data["material_id"] is not None
                or data["dimensions_cm"] is not None
            ):
                raise ValueError("invalid_light_operation")
            if (
                type(data["intensity"]) not in (int, float)
                or not math.isfinite(data["intensity"])
                or not 1 <= data["intensity"] <= 100000
            ):
                raise ValueError("invalid_light_intensity")
            dims = None
        else:
            raise ValueError("unsupported_graphics_operation")
        return cls(
            data["id"],
            "graphics",
            data["kind"],
            data["shape"],
            data["material_id"],
            vector(data["location_cm"], -100000, 100000),
            vector(data["rotation_deg"], -360, 360),
            dims,
            data["intensity"],
        )


@dataclass(frozen=True)
class ScenePlan:
    schema_version: int
    scene_id: str
    revision: int
    parent_manifest_sha256: str | None
    level: str
    materials: tuple[MaterialSpec, ...]
    actors: tuple[ActorSpec, ...]
    protected_engineer_ids: tuple[str, ...]

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ScenePlan:
        exact(
            data,
            {
                "schema_version",
                "scene_id",
                "revision",
                "parent_manifest_sha256",
                "level",
                "materials",
                "actors",
                "protected_engineer_ids",
            },
        )
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("unsupported_scene_contract_version")
        if not isinstance(data["scene_id"], str) or not ID.fullmatch(data["scene_id"]):
            raise ValueError("invalid_scene_id")
        if type(data["revision"]) is not int or not 1 <= data["revision"] <= 100:
            raise ValueError("invalid_scene_revision")
        parent = data["parent_manifest_sha256"]
        if (data["revision"] == 1 and parent is not None) or (
            data["revision"] > 1 and (not isinstance(parent, str) or not SHA.fullmatch(parent))
        ):
            raise ValueError("unverified_scene_parent")
        if (
            data["level"] not in LEVELS
            or not isinstance(data["materials"], list)
            or not 1 <= len(data["materials"]) <= 12
        ):
            raise ValueError("invalid_scene_quality_or_materials")
        if not isinstance(data["actors"], list) or not 1 <= len(data["actors"]) <= 40:
            raise ValueError("scene_actor_budget_exceeded")
        materials = tuple(MaterialSpec.parse(m) for m in data["materials"])
        actors = tuple(ActorSpec.parse(a) for a in data["actors"])
        material_ids = {m.id for m in materials}
        actor_ids = {a.id for a in actors}
        if len(material_ids) != len(materials) or len(actor_ids) != len(actors):
            raise ValueError("duplicate_scene_identifier")
        if any(a.material_id not in material_ids for a in actors if a.kind == "mesh"):
            raise ValueError("unresolved_material_reference")
        protected = data["protected_engineer_ids"]
        if not isinstance(protected, list) or any(
            not isinstance(p, str) or not ID.fullmatch(p) for p in protected
        ):
            raise ValueError("invalid_engineer_dependency_ids")
        if len(set(protected)) != len(protected) or actor_ids.intersection(protected):
            raise ValueError("engineer_identifier_collision")
        return cls(
            1,
            data["scene_id"],
            data["revision"],
            parent,
            data["level"],
            materials,
            actors,
            tuple(protected),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(json.dumps(asdict(self)))
        return data

    @property
    def map_path(self) -> str:
        return f"/Game/APEXGraphics/{self.scene_id}/R{self.revision:03d}/Scene"

    @classmethod
    def demo(cls, *, level: str = "prototype") -> ScenePlan:
        def mesh(
            name: str, shape: str, material: str, location: list[int], dimensions: list[int]
        ) -> dict[str, Any]:
            return {
                "id": name,
                "owner": "graphics",
                "kind": "mesh",
                "shape": shape,
                "material_id": material,
                "location_cm": location,
                "rotation_deg": [0, 0, 0],
                "dimensions_cm": dimensions,
                "intensity": None,
            }

        actors = [
            mesh("road", "cube", "asphalt", [0, 0, -10], [2400, 600, 20]),
            mesh("building", "cube", "concrete", [300, 700, 300], [600, 500, 600]),
            mesh("bollard_a", "cylinder", "metal", [-500, 390, 40], [30, 30, 80]),
            mesh("bollard_b", "cylinder", "metal", [0, 390, 40], [30, 30, 80]),
            mesh("decor_planter", "cube", "concrete", [-600, 750, 30], [160, 100, 60]),
        ]
        actors.extend(
            [
                {
                    "id": "sun",
                    "owner": "graphics",
                    "kind": "directional_light",
                    "shape": None,
                    "material_id": None,
                    "location_cm": [0, 0, 800],
                    "rotation_deg": [-40, -25, 0],
                    "dimensions_cm": None,
                    "intensity": 45000,
                },
                {
                    "id": "fill",
                    "owner": "graphics",
                    "kind": "point_light",
                    "shape": None,
                    "material_id": None,
                    "location_cm": [-500, 600, 400],
                    "rotation_deg": [0, 0, 0],
                    "dimensions_cm": None,
                    "intensity": 2000,
                },
            ]
        )
        return cls.parse(
            {
                "schema_version": 1,
                "scene_id": "urban_demo",
                "revision": 1,
                "parent_manifest_sha256": None,
                "level": level,
                "materials": [
                    {"id": "asphalt", "base_color": [0.035, 0.04, 0.045], "roughness": 0.8, "metallic": 0},
                    {"id": "concrete", "base_color": [0.5, 0.45, 0.36], "roughness": 0.65, "metallic": 0},
                    {"id": "metal", "base_color": [0.25, 0.27, 0.3], "roughness": 0.3, "metallic": 0.8},
                ],
                "actors": actors,
                "protected_engineer_ids": [],
            }
        )


@dataclass(frozen=True)
class SceneCriteria:
    level: str
    position_tolerance_cm: float = 0.2
    dimension_tolerance_cm: float = 1
    max_triangles: int = 100000
    max_attempts_per_defect: int = 3
    min_fps: float | None = None
    calibrated_reference_required: bool = False
    texture_maps_required: bool = False
    captures: int = 4

    def __post_init__(self) -> None:
        if self.level not in LEVELS or self.captures != 4:
            raise ValueError("invalid_scene_criteria")
        if type(self.max_attempts_per_defect) is not int or not 1 <= self.max_attempts_per_defect <= 10:
            raise ValueError("invalid_scene_correction_budget")
        if not 0 < self.position_tolerance_cm <= 5 or not 0 < self.dimension_tolerance_cm <= 10:
            raise ValueError("invalid_scene_tolerance")
        if not 100 <= self.max_triangles <= 1000000 or (
            self.min_fps is not None and not 1 <= self.min_fps <= 240
        ):
            raise ValueError("invalid_scene_performance_budget")

    @classmethod
    def for_level(cls, level: str, **overrides: Any) -> SceneCriteria:
        presets = {
            "prototype": {},
            "jouable": {"min_fps": 30},
            "soigne": {"min_fps": 60, "calibrated_reference_required": True, "texture_maps_required": True},
            "photorealiste": {
                "min_fps": 60,
                "calibrated_reference_required": True,
                "texture_maps_required": True,
            },
        }
        if level not in presets:
            raise ValueError("invalid_scene_quality_level")
        return cls(level=level, **{**presets[level], **overrides})
