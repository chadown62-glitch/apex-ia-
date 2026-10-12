"""Technical OracleProvider gate: engine evidence takes precedence over model assertions."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from apex.game_engineer.contract import EngineCriteria
from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task


class UnrealTechnicalOracle(OracleProvider):
    def __init__(
        self, engine_probe: dict[str, Any], asset_report: dict[str, Any], criteria: EngineCriteria
    ) -> None:
        self.probe, self.asset, self.criteria = engine_probe, asset_report, criteria

    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        return self.preflight_result()

    def preflight_result(self) -> OracleResult:
        """No SandboxResult is fabricated when an engine never started."""
        defects = []
        if self.asset.get("status") != "verified_intake":
            defects.append(
                {"id": "asset_intake", "correction": "return an intact agreed VehicleAssetContract bundle"}
            )
        unavailable = []
        if self.probe.get("status") == "blocked":
            unavailable.append("real_unreal_editor_sdk_plugins_missing")
        rig = self.asset.get("manifest", {}).get("rig", {})
        if not rig.get("skeletal_rig") or not rig.get("physics_asset"):
            unavailable.append("chaos_skeletal_rig_and_physics_asset_not_qualified")
        # No native TaskLoop adapter or qualified automation-driving runner exists in this lot.
        # Even a successful intake process and producer/model booleans cannot satisfy these checks.
        unavailable.extend(
            [
                "actual_unreal_compilation",
                "actual_editor_import_scale_axes_pivots_materials",
                "level_load_and_vehicle_components",
                "collision_suspension_physics",
                "camera_and_input",
                "automated_driving_scenario",
                "blocking_error_log_check",
            ]
        )
        if self.criteria.min_fps is not None:
            unavailable.append("measured_target_hardware_fps")
        detail = {
            "criteria": asdict(self.criteria),
            "defects": defects,
            "unavailable": unavailable,
            "engine_probe": self.probe,
            "model_claims_accepted": False,
            "scope": "objective preflight gate, full UE functional judge not qualified",
        }
        return OracleResult(OracleVerdict.FAIL if defects else OracleVerdict.UNKNOWN, json.dumps(detail))
