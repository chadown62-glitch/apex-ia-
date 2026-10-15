from pathlib import Path

import pytest

from apex.game_engineer.__main__ import prepare
from apex.game_engineer.contract import EngineCriteria, isolated_output
from apex.game_engineer.judge import UnrealTechnicalOracle
from apex.game_engineer.tools import discover_engine

BUNDLE = Path(__file__).resolve().parents[1] / "docs/game_engineer/evidence/received"


@pytest.mark.parametrize(
    "path",
    [
        r"C:\GameProject",
        r"c:\GAMEPROJECT\Content",
        "C:/GameProject/Source",
        "/mnt/c/GameProject/Content",
        r"\\?\C:\GameProject",
    ],
)
def test_protected_project_refused(path):
    with pytest.raises(ValueError, match="protected_user_project"):
        isolated_output(Path(path))


@pytest.mark.parametrize("level", ["prototype", "jouable", "soigne", "photorealiste"])
def test_levels_and_explicit_budget(level):
    criteria = EngineCriteria.for_level(level, max_attempts_per_defect=2, minimum_travel_m=3)
    assert criteria.max_attempts_per_defect == 2
    assert criteria.minimum_travel_m == 3
    assert criteria.level == level
    if level != "prototype":
        assert criteria.min_fps >= 30


def test_model_compile_and_drive_claims_cannot_pass():
    asset = {
        "status": "verified_intake",
        "manifest": {"rig": {"skeletal_rig": True, "physics_asset": True}},
        "compile_pass": True,
        "drive_pass": True,
    }
    result = UnrealTechnicalOracle(
        {"status": "available", "compile_pass": True}, asset, EngineCriteria()
    ).preflight_result()
    assert result.verdict.value == "unknown"
    assert "actual_unreal_compilation" in result.detail
    assert "automated_driving_scenario" in result.detail


def test_isolated_real_bundle_staging_keeps_unreal_unqualified(tmp_path):
    result = prepare(BUNDLE, tmp_path / "project-probe", EngineCriteria.for_level("photorealiste"))
    assert result["intake"]["ok"]
    assert result["status"] == "blocked"
    assert result["judge"]["verdict"] == "unknown"
    assert result["engine_executed"] is False
    assert result["compile_executed"] is False
    assert result["driving_executed"] is False
    assert result["criteria"]["min_fps"] == 60
    assert Path(result["prepared_project"]).is_file()
    assert len(result["stored_artifacts"]) == 10
    with pytest.raises(ValueError, match="isolated_output_must_be_new"):
        prepare(BUNDLE, tmp_path / "project-probe", EngineCriteria())


def test_discovery_reports_real_observations_without_execution():
    result = discover_engine()
    assert result["execution_performed"] is False
    assert result["searched_roots"]
    assert result["ram_total_bytes"] > 0
    if result["editor"] is None:
        assert result["status"] == "blocked"
