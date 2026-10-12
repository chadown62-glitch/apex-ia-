"""Artistic direct UE workflow. A missing engine remains BLOCKED; preparation is not execution."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.config.schema import RecoveryPolicy
from apex.game_graphics.models import CoderSession, file_sha256
from apex.game_graphics.resources import resource_lease
from apex.game_graphics.ue.capabilities import probe
from apex.game_graphics.ue.contract import SceneCriteria, ScenePlan
from apex.game_graphics.ue.controller import propose_scene
from apex.game_graphics.ue.execution import run_editor
from apex.game_graphics.ue.judge import UnrealGraphicsOracle, compare_defects, compare_reference_views
from apex.game_graphics.ue.project import prepare, refuse_personal_project
from apex.game_graphics.ue.selection import GraphicsTask, select_method
from apex.judge.tier0 import Tier0Judge
from apex.oracles.model import OracleVerdict
from apex.recovery.model import FailureCategory, RecoveryAction
from apex.recovery.policy import decide
from apex.sandbox.model import ExecutionStatus
from apex.tasks.model import Task, TaskSplit


def seal_scene(root: Path, manifest: dict[str, Any], *, created: bool, judged: bool) -> dict[str, Any]:
    store = LocalFilesystemArtifactStore(root=str(root.parent / "artifact-store"))
    entries = []
    content = root / "project/Content/APEXGraphics"
    files = [root / "project/Requests/scene-plan.json"]
    if content.is_dir():
        files += [f for f in content.rglob("*") if f.is_file()]
    if (root / "captures").is_dir():
        files += list((root / "captures").glob("*.png"))
    if sum(f.stat().st_size for f in files) > 64_000_000:
        raise RuntimeError("restricted_scene_artifact_budget_exceeded")
    for path in files:
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise RuntimeError("scene_artifact_outside_workspace")
        metadata = store.put(path.read_bytes())
        if store.get(metadata.content_hash) != path.read_bytes():
            raise RuntimeError("scene_artifact_store_integrity_failed")
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
                "artifact": metadata.content_hash,
            }
        )
    manifest = {
        **manifest,
        "files": entries,
        "engine_created": created,
        "engine_judged": judged,
        "status": "objectively_checked"
        if judged
        else "created_unqualified"
        if created
        else "prepared_unverified",
    }
    manifest["planned_geometry_method"] = "native_unreal_basic_mesh_assembly"
    manifest["geometry_method"] = "native_unreal_basic_mesh_assembly" if created else "not_generated"
    (root / "scene-contract.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def run_scene(
    plan: ScenePlan,
    root: Path,
    capabilities: dict[str, Any],
    criteria: SceneCriteria,
    *,
    execute: bool,
    parent: Path | None = None,
) -> dict[str, Any]:
    root = refuse_personal_project(root)
    if root.exists():
        raise ValueError("output_exists_preserve_previous_versions")
    root.mkdir(parents=True)
    method = select_method(GraphicsTask("environment", True, level=plan.level), capabilities)
    attempts: list[dict[str, Any]] = []
    policy = RecoveryPolicy(max_attempts=criteria.max_attempts_per_defect, resource_exhausted_max_retries=1)
    previous: list[dict[str, Any]] | None = None
    counts: dict[str, int] = {}
    result: dict[str, Any] = {
        "contract": "SceneIntegrationReport",
        "schema_version": 1,
        "scene_id": plan.scene_id,
        "requested_revision": plan.revision,
        "status": "blocked",
        "selection": method,
        "criteria": asdict(criteria),
        "engine_executed": False,
        "captures_created": False,
        "attempts": attempts,
        "photorealism_qualified": False,
        "gameplay_modified": False,
    }
    for number in range(1, criteria.max_attempts_per_defect + 1):
        folder = root / f"attempt-{number:02d}"
        manifest = prepare(folder, plan, parent=parent)
        entry: dict[str, Any] = {
            "number": number,
            "scene_id": plan.scene_id,
            "revision": plan.revision,
            "root": str(folder),
            "stage": "prepared",
            "engine_executed": False,
        }
        attempts.append(entry)
        if not execute or not capabilities["unreal"]["available"]:
            seal_scene(folder, manifest, created=False, judged=False)
            result["reason"] = (
                "unreal_editor_missing"
                if not capabilities["unreal"]["available"]
                else "explicit_preparation_only"
            )
            break
        # The first run qualifies the candidate adapter; no earlier engine success is assumed.
        entry["qualification_run"] = True
        build_execution, built = run_editor(capabilities, folder, "build")
        result["engine_executed"] = entry["engine_executed"] = True
        if build_execution.status is not ExecutionStatus.SUCCESS or built.get("status") != "created":
            result["reason"] = "real_editor_build_failed_or_api_unqualified"
            entry["build_execution"] = build_execution.to_dict()
            seal_scene(folder, manifest, created=False, judged=False)
            break
        inspected_execution, inspected = run_editor(capabilities, folder, "inspect")
        oracle = UnrealGraphicsOracle(
            folder, plan, criteria, built, inspected, build_execution=build_execution
        )
        task = Task.new(
            split=TaskSplit.TRAIN,
            specialty="game_graphics",
            title="Native Unreal graphics scene",
            description="Independent editor reload and native views",
            command=("UnrealEditor",),
            created_by="graphics-ue",
        )
        verdict = replace(
            Tier0Judge(oracle).evaluate(sandbox_result=inspected_execution, task=task),
            notes="UE-native graphics oracle; calibrated aesthetics/game FPS unavailable",
        )
        entry["verdict"] = verdict.oracle_result.verdict.value
        entry["inspection_execution"] = inspected_execution.to_dict()
        detail = (
            json.loads(verdict.oracle_result.detail) if verdict.oracle_result.detail.startswith("{") else {}
        )
        defects = detail.get("defects", [])
        entry["defects"] = defects
        checked = verdict.oracle_result.verdict is OracleVerdict.PASS
        seal_scene(folder, manifest, created=True, judged=checked)
        result["captures_created"] = len(inspected.get("captures", [])) == 4
        if checked:
            result["status"], result["reason"] = "pass", "native_scene_objective_criteria_satisfied"
            break
        if verdict.oracle_result.verdict is not OracleVerdict.FAIL:
            result["reason"] = "required_native_or_visual_measurements_unavailable"
            break
        if previous:
            stop = compare_defects(previous, defects)
            if stop:
                result["reason"] = stop
                break
        for defect in defects:
            key = defect["id"] + ":" + defect.get("actor", "")
            counts[key] = counts.get(key, 0) + 1
        recovery = decide(
            FailureCategory.INCORRECT_SOLUTION,
            attempt_number=max(counts.values(), default=number),
            policy=policy,
            previous_failure=verdict.oracle_result.detail,
        )
        entry["recovery"] = recovery.to_dict()
        if recovery.action is not RecoveryAction.RETRY:
            result["reason"] = "defect_attempt_budget_exhausted"
            break
        if not defects or any(
            d["id"] not in ("pose", "size", "material", "light") or not d.get("actor") for d in defects
        ):
            result["reason"] = "no_qualified_targeted_repair_for_this_native_defect"
            break
        # Versioned reconstruction of this graphics-only level with corrected native operations.
        # Does not claim safe mutation of an integrated Engineer/gameplay map.
        entry["correction_request"] = {
            "origin": "unreal",
            "actors": sorted({d["actor"] for d in defects}),
            "method": "reapply agreed native graphic parameters in a new isolated scene revision",
            "previous_files_preserved": True,
            "live_integrated_level_modified": False,
        }
        previous = defects
        parent = folder / "scene-contract.json"
        next_data = plan.to_dict()
        next_data.update(revision=plan.revision + 1, parent_manifest_sha256=file_sha256(parent))
        plan = ScenePlan.parse(next_data)
    result["defect_attempts"] = counts
    result["revision"] = plan.revision
    result["capabilities"] = capabilities
    result["scene_asset_contract"] = str(folder / "scene-contract.json")
    result["integration_report_contract"] = "SceneIntegrationReport v1"
    result["integration_to_game_engineer"] = (
        "versioned IDs and SHA; functional dependencies require independent Engineer revalidation"
    )
    (root / "integration-report.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument(
        "--level", required=True, choices=("prototype", "jouable", "soigne", "photorealiste")
    )
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--prompt")
    parser.add_argument(
        "--demo", action="store_true", help="Explicit fixed operation fixture; not neural generation"
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--references", type=Path, help="View/path/SHA JSON; raw metrics never certify aesthetics"
    )
    parser.add_argument(
        "--task-kind",
        default="environment",
        choices=(
            "environment",
            "lighting",
            "materials",
            "vehicle",
            "mesh",
            "uv_retopology",
            "landscape",
            "pcg",
            "modeling_tools",
        ),
    )
    parser.add_argument("--complex-mesh", action="store_true")
    parser.add_argument("--asset-output-only", action="store_true")
    args = parser.parse_args()
    own_pipeline = False
    try:
        refuse_personal_project(args.root)
        if args.root.exists():
            raise ValueError("output_exists_preserve_previous_versions")
        overrides = json.loads(args.criteria.read_text()) if args.criteria else {}
        if args.references:
            overrides["calibrated_reference_required"] = True
        criteria = SceneCriteria.for_level(args.level, **overrides)
        capabilities = probe(args.engine_root)
        selected = select_method(
            GraphicsTask(args.task_kind, not args.asset_output_only, args.complex_mesh, args.level),
            capabilities,
        )
        if args.task_kind not in ("environment", "lighting", "materials") or selected["method"] != "unreal":
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "selection": selected,
                        "engine_executed": False,
                        "reason": "adapter_unqualified_scene_substitution_refused",
                    },
                    indent=2,
                )
            )
            return 2
        if sum(bool(v) for v in (args.plan, args.prompt, args.demo)) != 1:
            raise ValueError("choose_exactly_one_plan_prompt_or_explicit_demo")
        with resource_lease(args.runtime / "resource.lock"):
            controller = None
            if args.plan:
                plan = ScenePlan.parse(json.loads(args.plan.read_text()))
                if plan.level != args.level:
                    raise ValueError("requested_quality_changed_by_plan")
            elif args.demo:
                plan = ScenePlan.demo(level=args.level)
            else:
                if not capabilities["unreal"]["available"] and not args.prepare_only:
                    raise RuntimeError("unreal_editor_missing_no_point_loading_model_for_execution")
                session = CoderSession(args.runtime / "coder-model.json", args.runtime / "ue-controller")
                with session:
                    plan, controller = propose_scene(
                        session.endpoint,
                        args.prompt,
                        level=args.level,
                        record_path=args.runtime / "ue-controller/raw-generation.json",
                    )
                    session.sample_memory()
                controller["lifecycle"] = session.facts
                if not session.facts.get("process_stopped_verified"):
                    raise RuntimeError("graphics_model_release_unverified")
            own_pipeline = True
            result = run_scene(
                plan, args.root, capabilities, criteria, execute=not args.prepare_only, parent=args.parent
            )
            if args.references:
                folder = Path(result["attempts"][-1]["root"])
                result["reference_comparison"] = compare_reference_views(folder, args.references)
            if controller:
                (args.root / "controller.json").write_text(json.dumps(controller, indent=2, default=str))
            result["plan_source"] = (
                "real_local_neural_operations"
                if controller
                else "user_plan"
                if args.plan
                else "explicit_fixed_demo_operations"
            )
    except Exception as exc:
        started = own_pipeline and any(args.root.glob("attempt-*/*-started.json"))
        result = {
            "status": "blocked",
            "reason": type(exc).__name__ + ": " + str(exc),
            "engine_executed": bool(started),
        }
        if own_pipeline and args.root.is_dir():
            (args.root / "blocked-error.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
