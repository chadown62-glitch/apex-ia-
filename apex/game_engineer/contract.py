"""Explicit technical requirements; JSON is the cross-branch asset boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_SCHEMA_SHA256 = "1ba530565c0869760aac9fa08273f033d449401cb5bb5e880cee8874ba29ae51"
WHEELS = ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right")


@dataclass(frozen=True)
class EngineCriteria:
    level: str = "prototype"
    min_fps: float | None = None
    scenario_seconds: float = 5
    minimum_travel_m: float = 1
    max_attempts_per_defect: int = 3
    dimension_tolerance_cm: float = 4
    pivot_tolerance_cm: float = 0.2

    def __post_init__(self) -> None:
        if self.level not in ("prototype", "jouable", "soigne", "photorealiste"):
            raise ValueError("unknown_engine_quality_level")
        if not 1 <= self.max_attempts_per_defect <= 10 or not 1 <= self.scenario_seconds <= 120:
            raise ValueError("invalid_engine_budget")
        if self.min_fps is not None and not 1 <= self.min_fps <= 240:
            raise ValueError("invalid_fps_budget")
        if not 0 < self.minimum_travel_m <= 1000 or not 0 < self.pivot_tolerance_cm <= 5:
            raise ValueError("invalid_physics_requirement")
        if not 0 < self.dimension_tolerance_cm <= 10:
            raise ValueError("invalid_scale_tolerance")

    @classmethod
    def for_level(cls, level: str, **overrides: Any) -> EngineCriteria:
        presets = {
            "prototype": {},
            "jouable": {"min_fps": 30},
            "soigne": {"min_fps": 60, "scenario_seconds": 10},
            "photorealiste": {"min_fps": 60, "scenario_seconds": 15},
        }
        if level not in presets:
            raise ValueError("unknown_engine_quality_level")
        return cls(level=level, **{**presets[level], **overrides})


def isolated_output(path: Path) -> Path:
    """Refuse the protected personal project, including its Windows/WSL spellings."""
    for spelling in (str(path), str(path.resolve())):
        normal = spelling.replace("\\", "/").lower().replace("//?/", "").rstrip("/")
        for forbidden in ("c:/gameproject", "/mnt/c/gameproject"):
            if normal == forbidden or normal.startswith(forbidden + "/"):
                raise ValueError("protected_user_project")
    if path.is_symlink():
        raise ValueError("output_symlink_refused")
    real = path.resolve()
    if len(real.parts) < 3 or real == Path("/"):
        raise ValueError("isolated_project_root_required")
    return real
