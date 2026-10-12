"""Actual Blender fixtures, reimports, file corruption and a real targeted correction."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from apex.game_graphics.__main__ import inspect_and_judge, run_pipeline
from apex.game_graphics.blender import manifest_vehicle
from apex.game_graphics.contract import QualityCriteria, VehicleSpec


@pytest.fixture(scope="module")
def real_asset(tmp_path_factory):
    if shutil.which("blender") is None:
        pytest.skip("Real Blender absent; no substitute execution")
    root = tmp_path_factory.mktemp("real-blender") / "baseline"
    report = run_pipeline(
        VehicleSpec.example(),
        root,
        QualityCriteria.for_level("jouable"),
        prompt="Own red coupe prototype; test tool-only execution",
    )
    assert report["status"] == "pass", report
    return Path(report["artifact_root"])


def test_actual_blend_and_glb_and_lod_are_closed(real_asset):
    for name in ("blend", "glb", "lod1"):
        report = json.loads((real_asset / (name + "-inspection.json")).read_text())
        assert all(
            obj["nonmanifold_edges"] == 0 and obj["degenerate_faces"] == 0
            for obj in report["objects"].values()
        )
    full = json.loads((real_asset / "glb-inspection.json").read_text())
    lod = json.loads((real_asset / "lod1-inspection.json").read_text())
    assert lod["triangles"] < full["triangles"]


def test_photorealism_cannot_be_asserted_by_producer(real_asset, tmp_path):
    dest = tmp_path / "copy"
    shutil.copytree(real_asset, dest)
    data = json.loads((dest / "manifest.json").read_text())
    data["visual_judge_calibrated"] = True
    (dest / "manifest.json").write_text(json.dumps(data))
    result = inspect_and_judge(dest, VehicleSpec.example(), QualityCriteria.for_level("photorealiste"))
    assert result["verdict"] == "unknown"
    assert "calibrated visual/reference" in result["detail"]


def test_corrupt_artifact_cannot_pass(real_asset, tmp_path):
    dest = tmp_path / "copy"
    shutil.copytree(real_asset, dest)
    with (dest / "vehicle.glb").open("ab") as handle:
        handle.write(b"corrupt appended bytes")
    result = inspect_and_judge(dest, VehicleSpec.example(), QualityCriteria())
    assert result["verdict"] != "pass"


def test_blank_render_fails_even_with_updated_hash(real_asset, tmp_path):
    dest = tmp_path / "copy"
    shutil.copytree(real_asset, dest)
    Image.new("RGB", (640, 420), "white").save(dest / "front.png")
    manifest_vehicle(dest, VehicleSpec.example())
    result = inspect_and_judge(dest, VehicleSpec.example(), QualityCriteria())
    assert result["verdict"] == "fail"
    assert "invalid_render" in result["detail"]


def test_real_pivot_failure_and_targeted_repair(tmp_path):
    if shutil.which("blender") is None:
        pytest.skip("Real Blender absent")
    report = run_pipeline(
        VehicleSpec.example(),
        tmp_path / "fault",
        QualityCriteria(),
        prompt="Own coupe; injected 0.12m wheel mesh offset",
        defect="pivot",
    )
    assert report["status"] == "pass", report
    assert [entry["verdict"] for entry in report["attempts"]] == ["fail", "pass"]
    first = report["attempts"][0]
    assert first["recovery"]["action"] == "retry"
    assert any(d["distance_m"] > 0.1 for d in first["defects"])
    assert Path(first["root"]).is_dir()


def test_actual_degenerate_geometry_is_rejected(tmp_path):
    if shutil.which("blender") is None:
        pytest.skip("Real Blender absent")
    report = run_pipeline(
        VehicleSpec.example(),
        tmp_path / "degenerate",
        QualityCriteria(),
        prompt="Own coupe with a real collinear triangle fault fixture",
        defect="degenerate",
    )
    assert report["status"] == "blocked"
    assert report["attempts"][0]["verdict"] == "fail"
    assert any(d["id"] == "invalid_geometry" for d in report["attempts"][0]["defects"])
