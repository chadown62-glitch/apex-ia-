"""Vehicle parameters and explicit quality requirements, separate from model predictions."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

WHEELS = ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right")


@dataclass(frozen=True)
class VehicleSpec:
    length_m: float
    width_m: float
    height_m: float
    wheelbase_m: float
    wheel_radius_m: float
    wheel_width_m: float
    mass_kg: float
    body_color: tuple[float, float, float]
    paint_metallic: float
    paint_roughness: float

    def __post_init__(self) -> None:
        bounds = {
            "length_m": (3.0, 6.0),
            "width_m": (1.4, 2.5),
            "height_m": (0.9, 2.0),
            "wheelbase_m": (1.8, 4.0),
            "wheel_radius_m": (0.25, 0.5),
            "wheel_width_m": (0.12, 0.4),
            "mass_kg": (600, 3500),
            "paint_metallic": (0, 1),
            "paint_roughness": (0.02, 1),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid {name}")
        if self.wheelbase_m + 2 * self.wheel_radius_m >= self.length_m:
            raise ValueError("wheels exceed body length")
        if len(self.body_color) != 3 or any(
            type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in self.body_color
        ):
            raise ValueError("invalid body_color")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VehicleSpec:
        if set(data) != {f.name for f in fields(cls)}:
            raise ValueError("vehicle spec requires exact fields")
        return cls(**{**data, "body_color": tuple(data["body_color"])})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def example(cls) -> VehicleSpec:
        return cls(4.5, 1.9, 1.35, 2.65, 0.34, 0.22, 1350, (0.62, 0.015, 0.008), 0.65, 0.18)


@dataclass(frozen=True)
class QualityCriteria:
    level: str = "prototype"
    max_triangles: int = 100_000
    dimension_tolerance_m: float = 0.04
    pivot_tolerance_m: float = 0.002
    max_distinct_defect_attempts: int = 3
    minimum_views: int = 4
    required_lods: int = 0
    required_texture_size: int = 0
    require_calibrated_visual_reference: bool = False

    def __post_init__(self) -> None:
        if self.level not in ("prototype", "jouable", "soigne", "photorealiste"):
            raise ValueError("unknown quality level")
        if not 1000 <= self.max_triangles <= 1_000_000 or not 1 <= self.max_distinct_defect_attempts <= 10:
            raise ValueError("invalid budget")
        if not 0 < self.dimension_tolerance_m <= 0.1 or not 0 < self.pivot_tolerance_m <= 0.05:
            raise ValueError("invalid tolerances")
        if not 4 <= self.minimum_views <= 12:
            raise ValueError("at least four views required")
        if not 0 <= self.required_lods <= 4 or self.required_texture_size not in (
            0,
            1024,
            2048,
            4096,
            8192,
        ):
            raise ValueError("invalid LOD or texture requirements")

    @classmethod
    def for_level(cls, level: str, **overrides: Any) -> QualityCriteria:
        presets = {
            "prototype": {},
            "jouable": {"max_triangles": 60_000, "required_lods": 1},
            "soigne": {
                "required_lods": 2,
                "required_texture_size": 2048,
                "require_calibrated_visual_reference": True,
            },
            "photorealiste": {
                "required_lods": 2,
                "required_texture_size": 4096,
                "require_calibrated_visual_reference": True,
            },
        }
        if level not in presets:
            raise ValueError("unknown quality level")
        return cls(level=level, **{**presets[level], **overrides})
