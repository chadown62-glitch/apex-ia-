"""Reproducible real Blender benchmark; serial models/processes share the PR12 lease."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
from dataclasses import asdict
from pathlib import Path

import psutil

from apex.config.schema import RecoveryPolicy
from apex.game_graphics.__main__ import compare_defects, defect_key
from apex.game_graphics.contract import QualityCriteria
from apex.game_graphics.models import CoderSession, file_sha256
from apex.game_graphics.resources import resource_lease
from apex.game_graphics.studio.contract import validate_correction_scope
from apex.game_graphics.studio.controller import propose_plan
from apex.game_graphics.studio.dataset import record_example, training_readiness
from apex.game_graphics.studio.execution import (
    ROOT,
    SCRIPTS,
    create_revision,
    inspect_revision,
    manifest_revision,
    refuse_personal_project,
    run_blender,
)
from apex.game_graphics.studio.judge import StudioGeometryOracle, calibration_report, measure_pair
from apex.judge.tier0 import Tier0Judge
from apex.recovery.model import FailureCategory, RecoveryAction
from apex.recovery.policy import decide
from apex.sandbox.model import ExecutionStatus
from apex.tasks.model import Task, TaskSplit

BENCHMARK = ROOT / "docs/game_graphics/studio/benchmark-v1.json"


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, default=str))


def calibrate(root, *, cancel_event=None, timeout_s=600, rss_limit_mib=12000):
    execution = run_blender(
        "render_asset.py",
        ["--calibration", "--family", "chair", "--output", str(root)],
        root / "calibration.log",
        cancel_event=cancel_event,
        timeout_s=timeout_s,
        rss_limit_mib=rss_limit_mib,
    )
    if execution.status is not ExecutionStatus.SUCCESS:
        raise RuntimeError("calibration_render_failed")
    design = json.loads((root / "design.json").read_text())
    rows = []
    for case in design["cases"]:
        for view in ("front", "rear"):
            path = root / case["id"] / ("id_" + view + ".png")
            measured = measure_pair(
                path,
                red_object="fixture_left",
                green_object="fixture_right",
                view=view,
                reference_id=case["id"],
                expected_sha256=file_sha256(path),
            )
            rows.append(
                {
                    "id": case["id"] + ":" + view,
                    "family": "calibration_curved_beams",
                    "expected_misaligned": case["expected_misaligned"],
                    "native_render_verified": True,
                    "measurement": measured,
                }
            )
    report = calibration_report(rows, predeclared_manifest_sha256=file_sha256(root / "design.json"))
    report.update(
        false_positives=report["counts"]["false_positive"],
        false_negatives=report["counts"]["false_negative"],
        accepted_cases=report["counts"]["true_negative"],
        rejected_cases=report["counts"]["true_positive"],
    )
    report["native_execution"] = execution.to_dict()
    report["measurements"] = rows
    dump(root / "calibration.json", report)
    return report


def visual_alignment(root, calibration):
    shutil.copyfile(calibration / "calibration.json", root / "calibration.json")
    record = json.loads((root / "calibration.json").read_text())
    measurements = []
    for view in ("front", "rear"):
        path = root / ("id_" + view + ".png")
        measured = measure_pair(
            path,
            red_object="arm_left",
            green_object="arm_right",
            view=view,
            reference_id="original-lounge-chair-v1",
            expected_sha256=file_sha256(path),
            pixels_per_meter=960 / 2.36,
        )
        for defect in measured["defects"]:
            defect.pop("estimated_target_delta_z_m", None)
            defect.pop("estimate_note", None)
            defect["distance_m"] = defect["measured_y_delta_pixels"] / (960 / 2.36)
            defect["confidence"] = {
                "kind": "small_native_fixture_calibration",
                "calibrated": True,
                "scope": "isolated mirrored pair, level orthographic cameras; not general beauty",
            }
        measurements.append(measured)
    visual = {
        "scope": "isolated_paired_component_alignment",
        "required": True,
        "calibrated": record["status"] == "pass_in_observed_scope",
        "status": "unknown"
        if any(m["measurement_status"] == "unknown" for m in measurements)
        else "fail"
        if any(m["defects"] for m in measurements)
        else "pass",
        "calibration": {"evidence_path": "calibration.json"},
        "measurements": measurements,
        "defects": [d for m in measurements for d in m["defects"]],
    }
    dump(root / "visual-alignment.json", visual)
    return visual


def judge_revision(root, family, criteria, benchmark, *, calibration=None, cancel_event=None):
    runs = inspect_revision(root, cancel_event=cancel_event)
    visual = visual_alignment(root, calibration) if family == "chair" and calibration else None
    case = next(c for c in benchmark["cases"] if c["family"] == family)
    manifest = manifest_revision(root, family, case["id"])
    if visual:
        for entry in manifest["files"]:
            if entry["path"] == "calibration.json":
                entry["role"] = "calibration"
        dump(root / "manifest.json", manifest)
    oracle = StudioGeometryOracle(
        root=root, family=family, criteria=criteria, benchmark=benchmark, visual=visual
    )
    execution = next((r for r in runs if r.status is not ExecutionStatus.SUCCESS), runs[0])
    task = Task.new(
        split=TaskSplit.HELDOUT if family == "chair" else TaskSplit.TRAIN,
        specialty="game_graphics",
        title="Studio " + family,
        description="Independent saved asset and multiview evidence",
        command=("blender",),
        created_by="game-graphics-studio",
    )
    verdict = Tier0Judge(oracle).evaluate(sandbox_result=execution, task=task)
    dump(
        root / "verdict.json",
        {
            "verdict": verdict.oracle_result.verdict.value,
            "judge": asdict(verdict),
            "inspection_runs": [r.to_dict() for r in runs],
        },
    )
    return json.loads((root / "judge-report.json").read_text())


def compact_hierarchy(path):
    # Full native hierarchy is retained; only task-relevant components enter the bounded model context.
    data = json.loads(path.read_text())
    selected = {
        "chassis",
        "front_grille",
        "mirror_left",
        "mirror_right",
        "rim_front_left",
        "windshield",
        "roof",
        "facade",
        "atelier_letters",
        "shop_window",
        "entry_door",
        "balcony_floor",
        "cornice",
        "window_left",
        "window_right",
        "seat_cushion",
        "back_cushion",
        "timber_back",
        "arm_left",
        "arm_right",
        "leg_front_left",
        "leg_front_right",
    }
    return {
        name: {
            "location_m": [round(v, 4) for v in obj["location_m"]],
            "dimensions_m": [round(v, 4) for v in obj["dimensions_m"]],
            "materials": obj["materials"],
        }
        for name, obj in data.items()
        if name in selected
    }


def bind_controller(root, folder, family, plan):
    """Bind applied operations to an actual accepted model response; preserve raw output."""
    candidates = list((root / "controller").glob("*.json")) + list(
        root.glob("correction-controller-*/*.json")
    )
    for path in candidates:
        data = json.loads(path.read_text())
        generated = data.get("plan", {})
        if (
            data.get("accepted")
            and generated.get("family") == family
            and all(op in generated.get("operations", []) for op in plan["operations"])
        ):
            dump(
                folder / "controller.json",
                {
                    "response": data,
                    "response_sha256": file_sha256(path),
                    "source_record": str(path.relative_to(root)),
                    "executed_plan": plan,
                    "exact_plan": generated == plan,
                },
            )
            return
    raise ValueError("applied_plan_has_no_verified_model_response")


def record(root, folder, family, benchmark, correction=None):
    case = next(c for c in benchmark["cases"] if c["family"] == family)
    return record_example(
        root / "dataset.jsonl",
        family=family,
        prompt=case["reference"],
        root=folder,
        script=SCRIPTS / "build_assets.py",
        correction=correction,
        provenance={
            "method": "procedural_blender",
            "reference": case["reference"],
            "rights": "original design; user-authorized APEX internal examples",
            "model": "Qwen2.5-Coder-7B-Instruct Q5_K_M catalog controller"
            if correction and correction.get("plan")
            else "none; trusted procedural Blender",
        },
    )


def build_baselines(root, benchmark, criteria):
    for case in benchmark["cases"]:
        folder = root / case["id"] / "v1"
        execution = create_revision(folder, case["family"])
        if execution.status is not ExecutionStatus.SUCCESS:
            raise RuntimeError("real_build_failed:" + str(folder))
        judgement = judge_revision(
            folder, case["family"], criteria, benchmark, calibration=root / "calibration"
        )
        record(root, folder, case["family"], benchmark)
        print(
            json.dumps(
                {"case": case["id"], "verdict": judgement["verdict"], "defects": judgement["defects"]}
            ),
            flush=True,
        )


def improve(root, runtime, benchmark, criteria):
    plans = {}
    instructions = {
        "vehicle": (
            "Add rear_spoiler, bevel front_grille width .004m segments3, "
            "subdivide mirror_left levels1, and set satin_alloy base_color [.46,.51,.55] "
            "roughness .32 metallic .92. Preserve other parts."
        ),
        "building": (
            "Repair degenerate topology of atelier_letters reported by the judge using "
            "repair_degenerate_topology, then add entrance_planters. Preserve other parts."
        ),
        "chair": "Add a lumbar cushion from the authorized catalog; preserve the chair silhouette.",
    }
    session = CoderSession(runtime / "coder-model.json", root / "controller")
    try:
        with session:
            for case in benchmark["cases"]:
                family = case["family"]
                base = root / case["id"] / "v1"
                plans[family] = propose_plan(
                    session.endpoint,
                    family=family,
                    instruction=instructions[family],
                    hierarchy=compact_hierarchy(base / "hierarchy.json"),
                    feedback=json.loads((base / "judge-report.json").read_text()),
                    output=root / "controller" / (family + ".json"),
                )
            session.sample_memory()
    finally:
        dump(root / "controller/lifecycle.json", session.facts)
    for case in benchmark["cases"]:
        family = case["family"]
        folder = root / case["id"] / "v2"
        execution = create_revision(
            folder, family, parent=root / case["id"] / "v1/asset.blend", plan=plans[family]
        )
        if execution.status is not ExecutionStatus.SUCCESS:
            raise RuntimeError("operation_failed:" + str(folder))
        bind_controller(root, folder, family, plans[family])
        judgement = judge_revision(folder, family, criteria, benchmark, calibration=root / "calibration")
        record(root, folder, family, benchmark, correction={"plan": plans[family]})
        print(json.dumps({"revision": str(folder), "verdict": judgement["verdict"]}), flush=True)


def compare_studio_defects(previous, current):
    # Keep the established recovery comparison, with view identity and worsening residuals.
    old = [{**d, "file": d.get("file", d.get("view", ""))} for d in previous]
    new = [{**d, "file": d.get("file", d.get("view", ""))} for d in current]
    lookup = {defect_key(d): d for d in old}
    for defect in new:
        before = lookup.get(defect_key(defect), {})
        if (
            "distance_m" in defect
            and "distance_m" in before
            and defect["distance_m"] > before["distance_m"] + 1e-6
        ):
            return "regression_detected"
    basic = compare_defects(old, new)
    if basic:
        return basic
    if (
        set(lookup) == {defect_key(d) for d in new}
        and all(d.get("id") == "paired_component_alignment" for d in new)
        and not any(
            lookup[defect_key(d)].get("measured_y_delta_pixels", 0) - d.get("measured_y_delta_pixels", 0)
            >= 1
            for d in new
        )
    ):
        return "no_improvement"
    return None


def correction_demo(root, runtime, benchmark, criteria):
    base = root / "lounge_chair/v2"
    if not base.exists():
        base = root / "lounge_chair/v1"
    hierarchy = compact_hierarchy(base / "hierarchy.json")
    reference = hierarchy["arm_left"]["location_m"]
    raised = [*reference]
    raised[2] += 0.12
    plan = {
        "family": "chair",
        "operations": [
            {"op": "set_location", "target": "arm_left", "params": {"location_m": raised}},
            {"op": "save_revision_and_render", "target": "scene", "params": {}},
        ],
    }
    broken = root / "lounge_chair/defect"
    result = create_revision(broken, "chair", parent=base / "asset.blend", plan=plan)
    if result.status is not ExecutionStatus.SUCCESS:
        raise RuntimeError("defect_injection_failed")
    previous = judge_revision(broken, "chair", criteria, benchmark, calibration=root / "calibration")
    record(root, broken, "chair", benchmark, correction={"intentional_defect": "arm_left +0.12m world Z"})
    if not any(d["id"] == "paired_component_alignment" for d in previous["defects"]):
        raise RuntimeError("injected_visual_defect_not_detected")
    attempts = []
    policy = RecoveryPolicy(
        max_attempts=criteria.max_distinct_defect_attempts, resource_exhausted_max_retries=1
    )
    parent = broken
    for number in range(1, criteria.max_distinct_defect_attempts + 1):
        decision = decide(
            FailureCategory.INCORRECT_SOLUTION,
            attempt_number=number,
            policy=policy,
            previous_failure=json.dumps(previous),
        )
        if decision.action is not RecoveryAction.RETRY:
            break
        folder = root / "lounge_chair" / ("corrected-" + str(number))
        session = CoderSession(
            runtime / "coder-model.json", root / ("correction-controller-" + str(number))
        )
        with session:
            plan = propose_plan(
                session.endpoint,
                family="chair",
                instruction="Correct only arm_left height according to the judge. "
                "set_location takes an ABSOLUTE FINAL location, NOT a relative displacement. "
                "Use EXACTLY the accepted reference location_m "
                + json.dumps(reference)
                + ". Keep all other objects and materials unchanged.",
                hierarchy=compact_hierarchy(parent / "hierarchy.json"),
                feedback=previous,
                output=root / ("correction-controller-" + str(number)) / "response.json",
            )
            session.sample_memory()
        dump(root / ("correction-controller-" + str(number)) / "lifecycle.json", session.facts)
        validate_correction_scope(plan, "arm_left", reference)
        result = create_revision(folder, "chair", parent=parent / "asset.blend", plan=plan)
        if result.status is not ExecutionStatus.SUCCESS:
            raise RuntimeError("targeted_correction_failed")
        bind_controller(root, folder, "chair", plan)
        after = judge_revision(folder, "chair", criteria, benchmark, calibration=root / "calibration")
        record(
            root,
            folder,
            "chair",
            benchmark,
            correction={"parent": str(parent.relative_to(root)), "plan": plan},
        )
        unchanged_settings = file_sha256(parent / "render-settings.json") == file_sha256(
            folder / "render-settings.json"
        )
        old_scene = json.loads((parent / "hierarchy.json").read_text())
        new_scene = json.loads((folder / "hierarchy.json").read_text())
        unrelated_unchanged = all(
            old_scene[n] == new_scene.get(n) for n in old_scene if n != "arm_left"
        ) and set(old_scene) == set(new_scene)
        target_geometry_unchanged = (
            old_scene["arm_left"]["geometry_sha256"] == new_scene["arm_left"]["geometry_sha256"]
        )
        if not unchanged_settings or not unrelated_unchanged or not target_geometry_unchanged:
            raise RuntimeError("correction_scope_regression")
        attempts.append(
            {
                "revision": str(folder.relative_to(root)),
                "verdict": after["verdict"],
                "plan": plan,
                "same_lights_cameras_render_settings": unchanged_settings,
                "unrelated_objects_unchanged": unrelated_unchanged,
                "only_target_transform_changed": target_geometry_unchanged,
                "recovery": decision.to_dict(),
            }
        )
        if after["verdict"] == "pass":
            break
        stop = compare_studio_defects(previous["defects"], after["defects"])
        if stop:
            attempts[-1]["stop_reason"] = stop
            break
        previous, parent = after, folder
    dump(
        root / "correction-demo.json",
        {
            "attempts": attempts,
            "max_attempts": criteria.max_distinct_defect_attempts,
            "status": "pass" if attempts and attempts[-1]["verdict"] == "pass" else "blocked",
            "geometry_correction": True,
            "lighting_change": False,
        },
    )


def hardware_observation():
    result = {
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "ram_available_bytes": psutil.virtual_memory().available,
        "ram_total_bytes": psutil.virtual_memory().total,
    }
    for name in ("cpu.max", "memory.max"):
        path = Path("/sys/fs/cgroup") / name
        result[name.replace(".", "_")] = path.read_text().strip() if path.exists() else None
    try:
        import torch

        result["cuda_available"] = torch.cuda.is_available()
        result["cuda_device_count"] = torch.cuda.device_count()
        result["torch_version"] = torch.__version__
    except ImportError:
        result["cuda_available"] = None
        result["cuda_device_count"] = None
    result["vram_measured"] = False
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--stage", choices=("baseline", "improve", "correction", "all"), default="all")
    parser.add_argument(
        "--level", choices=("prototype", "jouable", "soigne", "photorealiste"), default="prototype"
    )
    parser.add_argument("--criteria", type=Path)
    args = parser.parse_args()
    benchmark = json.loads(BENCHMARK.read_text())
    criteria = QualityCriteria.for_level(
        args.level, **(json.loads(args.criteria.read_text()) if args.criteria else {})
    )
    root = args.root.resolve()
    refuse_personal_project(root)
    root.mkdir(parents=True, exist_ok=True)
    with resource_lease(args.runtime / "resource.lock"):
        if args.stage in ("baseline", "all"):
            dump(root / "hardware.json", hardware_observation())
            shutil.copyfile(BENCHMARK, root / "benchmark-v1.json")
            calibrate(root / "calibration")
            build_baselines(root, benchmark, criteria)
        if args.stage in ("improve", "all"):
            improve(root, args.runtime, benchmark, criteria)
        if args.stage in ("correction", "all"):
            correction_demo(root, args.runtime, benchmark, criteria)
    dump(root / "training-readiness.json", training_readiness(root / "dataset.jsonl", benchmark))


if __name__ == "__main__":
    main()
