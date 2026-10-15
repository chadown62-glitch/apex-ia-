"""Concrete Blender pipeline with the existing oracle/judge/recovery contracts."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from apex.config.schema import RecoveryPolicy
from apex.game_graphics.blender import (
    SCRIPTS,
    build_vehicle,
    inspect_vehicle,
    manifest_vehicle,
    run_blender,
)
from apex.game_graphics.contract import QualityCriteria, VehicleSpec
from apex.game_graphics.controller import propose_vehicle
from apex.game_graphics.dataset import record_example
from apex.game_graphics.judge import BlenderGeometryOracle
from apex.game_graphics.models import CoderSession, triposr_readiness
from apex.game_graphics.resources import resource_lease
from apex.judge.tier0 import Tier0Judge
from apex.oracles.model import OracleVerdict
from apex.recovery.model import FailureCategory, RecoveryAction
from apex.recovery.policy import decide
from apex.sandbox.model import ExecutionStatus
from apex.tasks.model import Task, TaskSplit


def inspect_and_judge(root: Path, spec: VehicleSpec, criteria: QualityCriteria) -> dict[str, Any]:
    runs = [
        inspect_vehicle(root / name, root) for name in ("vehicle.blend", "vehicle.glb", "vehicle-lod1.glb")
    ]
    execution = next((r for r, _ in runs if r.status is not ExecutionStatus.SUCCESS), runs[0][0])
    oracle = BlenderGeometryOracle(
        root=root,
        spec=spec,
        criteria=criteria,
        blend_report=runs[0][1],
        glb_report=runs[1][1],
        lod_report=runs[2][1],
    )
    task = Task.new(
        split=TaskSplit.TRAIN,
        specialty="game_graphics",
        title="Vehicle geometry",
        description="Inspect the saved files independently",
        command=("blender",),
        created_by="game-graphics",
    )
    verdict = replace(
        Tier0Judge(oracle).evaluate(sandbox_result=execution, task=task),
        notes="Tier 0: BlenderGeometryOracle; aesthetic reference assessment unavailable",
    )
    result = {
        "verdict": verdict.oracle_result.verdict.value,
        "detail": verdict.oracle_result.detail,
        "judge": asdict(verdict),
        "inspection_runs": [asdict(r) for r, _ in runs],
    }
    (root / "verdict.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def defect_key(item: dict[str, Any]) -> str:
    return ":".join(str(item.get(k, "")) for k in ("id", "object", "format", "file"))


def compare_defects(old_defects: list[dict[str, Any]], defects: list[dict[str, Any]]) -> str | None:
    old = {defect_key(d): d for d in old_defects}
    keys = {defect_key(d) for d in defects}
    if keys - set(old):
        return "regression_detected"
    if keys == set(old) and not any(
        d.get("distance_m", 0) < old[defect_key(d)].get("distance_m", 0) - 1e-6 for d in defects
    ):
        return "no_improvement"
    return None


def run_pipeline(
    spec: VehicleSpec,
    root: Path,
    criteria: QualityCriteria,
    *,
    prompt: str,
    controller: dict[str, Any] | None = None,
    defect: str = "none",
    split: str = "train",
) -> dict[str, Any]:
    if root.exists() and any(root.iterdir()):
        raise ValueError("output_must_be_new_preserve_previous_versions")
    root.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    previous: Path | None = None
    policy = RecoveryPolicy(
        max_attempts=criteria.max_distinct_defect_attempts, resource_exhausted_max_retries=1
    )
    outcome = "blocked"
    reason = ""
    for number in range(1, criteria.max_distinct_defect_attempts + 1):
        folder = root / f"attempt-{number:02d}"
        if previous is None:
            execution = build_vehicle(spec, folder, defect=defect)
        else:
            folder.mkdir()
            shutil.copyfile(previous / "spec.json", folder / "spec.json")
            execution = run_blender(
                "build_vehicle.py",
                [
                    "--spec",
                    str(folder / "spec.json"),
                    "--output",
                    str(folder),
                    "--repair-pivots",
                    str(previous / "vehicle.blend"),
                ],
                folder / "build.log",
            )
        if execution.status is not ExecutionStatus.SUCCESS:
            reason = "blender_execution_failed"
            attempts.append({"number": number, "execution": asdict(execution)})
            break
        manifest_vehicle(folder, spec, controller)
        result = inspect_and_judge(folder, spec, criteria)
        detail = json.loads(result["detail"]) if result["detail"].startswith("{") else {}
        defects = detail.get("defects", [])
        keys = {defect_key(d) for d in defects}
        entry: dict[str, Any] = {
            "number": number,
            "root": str(folder),
            "verdict": result["verdict"],
            "defects": defects,
            "execution": asdict(execution),
        }
        attempts.append(entry)
        correction = attempts[-2].get("correction") if previous else None
        if (folder / "judge-report.json").is_file():
            record_example(
                root / "dataset.jsonl",
                family=root.name,
                split=split,
                prompt=prompt,
                root=folder,
                verdict=result["verdict"],
                script=SCRIPTS / "build_vehicle.py",
                correction=correction,
            )
        if result["verdict"] == OracleVerdict.PASS.value:
            outcome, reason = "pass", "objective_graphics_criteria_satisfied"
            break
        if result["verdict"] != OracleVerdict.FAIL.value:
            reason = "required_criteria_unavailable"
            break
        if previous:
            comparison = compare_defects(attempts[-2]["defects"], defects)
            if comparison:
                reason = comparison
                break
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        decision = decide(
            FailureCategory.INCORRECT_SOLUTION,
            attempt_number=max(counts.values(), default=number),
            policy=policy,
            previous_failure=result["detail"],
        )
        entry["recovery"] = decision.to_dict()
        if decision.action is not RecoveryAction.RETRY:
            reason = "defect_attempt_budget_exhausted"
            break
        if not defects or any(d["id"] != "wheel_geometry_center" for d in defects):
            reason = "no_verified_targeted_repair_for_detected_defect"
            break
        entry["correction"] = {
            "method": "local_mesh_recentering",
            "defects": sorted(keys),
            "scope": "wheel vertices only; preserve object pivots and prior artifacts",
        }
        previous = folder
    report = {
        "status": outcome,
        "reason": reason,
        "criteria": asdict(criteria),
        "attempts": attempts,
        "defect_attempts": counts,
        "artifact_root": attempts[-1].get("root") if attempts else None,
        "controller_kind": "real_local_neural_parameters"
        if controller
        else "explicit_procedural_tool_only",
        "geometry_kind": "trusted_procedural_blender",
        "aesthetic_certification": False,
    }
    (root / "pipeline.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument(
        "--level", choices=("prototype", "jouable", "soigne", "photorealiste"), required=True
    )
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--procedural-spec", type=Path, help="Explicit non-neural tool demonstration")
    args = parser.parse_args()
    overrides = json.loads(args.criteria.read_text()) if args.criteria else {}
    criteria = QualityCriteria.for_level(args.level, **overrides)
    controller = None
    try:
        with resource_lease(args.runtime / "resource.lock"):
            if args.procedural_spec:
                spec = VehicleSpec.from_dict(json.loads(args.procedural_spec.read_text()))
            else:
                session = CoderSession(
                    args.model_manifest or args.runtime / "coder-model.json",
                    args.runtime / "last-controller",
                )
                with session:
                    spec, controller = propose_vehicle(session.endpoint, args.prompt)
                    session.sample_memory()
                controller["lifecycle"] = session.facts
                if not session.facts.get("process_stopped_verified"):
                    raise RuntimeError("model_release_unverified")
            result = run_pipeline(spec, args.root, criteria, prompt=args.prompt, controller=controller)
            result["triposr"] = triposr_readiness()
    except Exception as exc:
        result = {
            "status": "blocked",
            "reason": type(exc).__name__ + ": " + str(exc),
            "criteria": asdict(criteria),
        }
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
