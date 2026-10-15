import os
import subprocess
import sys
from dataclasses import replace

import pytest

from apex.game_graphics.__main__ import compare_defects
from apex.game_graphics.contract import QualityCriteria, VehicleSpec
from apex.game_graphics.models import CoderSession
from apex.game_graphics.resources import resource_lease


@pytest.mark.parametrize(
    "field,value",
    [("length_m", float("nan")), ("mass_kg", True), ("wheelbase_m", 4.0), ("body_color", [2, 0, 0])],
)
def test_spec_rejects_unsafe_geometry(field, value):
    with pytest.raises(ValueError):
        replace(VehicleSpec.example(), **{field: value})


def test_json_requires_exact_fields():
    spec = VehicleSpec.example()
    assert VehicleSpec.from_dict(spec.to_dict()) == spec
    with pytest.raises(ValueError):
        VehicleSpec.from_dict({**spec.to_dict(), "python": "print('arbitrary code')"})


@pytest.mark.parametrize("level", ["prototype", "jouable", "soigne", "photorealiste"])
def test_quality_levels_preserve_explicit_overrides(level):
    criteria = QualityCriteria.for_level(level, pivot_tolerance_m=0.001, max_distinct_defect_attempts=2)
    assert criteria.level == level
    assert criteria.pivot_tolerance_m == 0.001
    assert criteria.max_distinct_defect_attempts == 2
    if level in ("soigne", "photorealiste"):
        assert criteria.require_calibrated_visual_reference
        assert criteria.required_texture_size >= 2048


def test_missing_model_is_blocked_without_procedural_fallback(tmp_path):
    with (
        pytest.raises(RuntimeError, match="coder_model_missing"),
        CoderSession(tmp_path / "missing.json", tmp_path),
    ):
        pytest.fail("a nonexistent model must not start")


def test_no_improvement_and_regression_stop_corrections():
    old = [{"id": "wheel_geometry_center", "object": "wheel_front_left", "distance_m": 0.12}]
    assert compare_defects(old, old) == "no_improvement"
    assert compare_defects(old, [{**old[0], "distance_m": 0.10}]) is None
    assert compare_defects(old, [{"id": "nonmanifold", "object": "chassis"}]) == "regression_detected"


def test_shared_model_profile_lock_blocks_different_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("apex.game_graphics.resources.check_external_workloads", lambda: None)
    code = """from pathlib import Path
from apex.game_graphics.resources import resource_lease
try:
    with resource_lease(Path('different-runtime/lock')):
        raise SystemExit(1)
except (BlockingIOError, PermissionError):
    raise SystemExit(0)
"""
    with resource_lease(tmp_path / "first-runtime" / "lock"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONPATH": os.getcwd()},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    assert result.returncode == 0, result.stderr
