"""Small Windows qualification run. No engine emulation, model download or gameplay edits."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import psutil

from apex.game_graphics.models import file_sha256
from apex.game_graphics.resources import check_external_workloads
from apex.game_graphics.ue.capabilities import probe
from apex.game_graphics.ue.contract import SceneCriteria, ScenePlan
from apex.game_graphics.ue.execution import run_editor, sample_gpu
from apex.game_graphics.ue.judge import UnrealGraphicsOracle, compare_defects
from apex.game_graphics.ue.project import prepare, refuse_personal_project
from apex.judge.tier0 import Tier0Judge
from apex.sandbox.model import ExecutionStatus
from apex.tasks.model import Task, TaskSplit

BLOCKED_CAPABILITIES = [
    "calibrated aesthetic/reference judgement and photorealism",
    "game renderer FPS and editor-attributed peak VRAM",
    "asset imports, PCG, Landscape, Nanite/Lumen, cities, vegetation systems",
    "gameplay-integrated levels, C++ and functional Blueprints",
    "neural mesh generation (#14, separate qualification)",
]


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")


def content_snapshot(project: Path) -> dict[str, str]:
    result = {}
    for path in sorted((project / "Content").rglob("*")):
        refuse_personal_project(path)
        if path.is_file():
            if path.stat().st_size > 64_000_000:
                raise RuntimeError("native_scene_file_budget_exceeded")
            result[path.relative_to(project).as_posix()] = file_sha256(path)
    return result


def seal(folder: Path, manifest: dict[str, Any], *, created: bool, judged: bool) -> None:
    manifest.update(
        engine_created=created,
        engine_judged=judged,
        geometry_method="native_unreal_basic_mesh_assembly" if created else "not_generated",
        status="objectively_checked"
        if judged
        else "created_unqualified"
        if created
        else "prepared_unverified",
        native_files=content_snapshot(folder / "project"),
    )
    write_json(folder / "scene-contract.json", manifest)


def run_stage(
    run_root: Path,
    label: str,
    plan: ScenePlan,
    criteria: SceneCriteria,
    capabilities: dict[str, Any],
    *,
    parent: Path | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    folder = run_root / label
    parent_receipt = parent / "scene-contract.json" if parent else None
    manifest = prepare(folder, plan, parent=parent_receipt)
    prior = content_snapshot(parent / "project") if parent else {}
    mode = "mutate" if operation else "build"
    if parent:
        if not prior or not operation:
            raise RuntimeError("actual_native_parent_required")
        shutil.copytree(parent / "project/Content", folder / "project/Content")
        if content_snapshot(folder / "project") != prior:
            raise RuntimeError("native_parent_copy_mismatch")
        job_path = folder / "project/Requests/mutate-job.json"
        job = json.loads(job_path.read_text())
        previous_map = f"/Game/APEXGraphics/{plan.scene_id}/R{plan.revision - 1:03d}/Scene"
        job.update(
            operation=operation,
            parent_map=previous_map,
            parent_map_sha256=prior["Content/" + previous_map.removeprefix("/Game/") + ".umap"],
        )
        write_json(job_path, job)
    entry: dict[str, Any] = {
        "stage": label,
        "revision": plan.revision,
        "status": "blocked",
        "verdict": "unknown",
    }
    print(f"{label}: real Unreal {mode}, then independent reopen/capture", flush=True)
    try:
        execution, built = run_editor(capabilities, folder, mode, timeout_s=900, max_sampled_rss_mb=12000)
        entry["build_execution"] = execution.to_dict()
        if execution.status is not ExecutionStatus.SUCCESS or built.get("status") != "created":
            entry["reason"] = "native_operation_failed_or_api_unqualified"
            entry["native_observation"] = built
            seal(folder, manifest, created=False, judged=False)
            return entry
        if not str(built.get("engine_version", "")).startswith("5.8."):
            raise RuntimeError("native_editor_report_is_not_ue5_8")
        native_map = folder / "project/Content" / (plan.map_path.removeprefix("/Game/") + ".umap")
        if not native_map.is_file():
            raise RuntimeError("native_saved_map_missing")
        saved = content_snapshot(folder / "project")
        inspected_execution, inspected = run_editor(capabilities, folder, "inspect", timeout_s=900)
        entry["inspection_execution"] = inspected_execution.to_dict()
        if inspected.get("status") == "inspected" and not str(
            inspected.get("engine_version", "")
        ).startswith("5.8."):
            raise RuntimeError("native_inspector_report_is_not_ue5_8")
        if execution.facts["pid"] == inspected_execution.facts["pid"]:
            raise RuntimeError("distinct_editor_pid_required_for_reopen")
        if not execution.facts["process_stopped_verified"]:
            raise RuntimeError("builder_must_stop_before_inspection")
        if content_snapshot(folder / "project") != saved:
            raise RuntimeError("independent_inspector_modified_native_content")
        oracle = UnrealGraphicsOracle(folder, plan, criteria, built, inspected, build_execution=execution)
        task = Task.new(
            split=TaskSplit.TRAIN,
            specialty="game_graphics",
            title="UE5.8 small scene qualification",
            description="Native save/reopen, four views and bounded road correction",
            command=("UnrealEditor",),
            created_by="graphics-ue-windows",
        )
        result = Tier0Judge(oracle).evaluate(sandbox_result=inspected_execution, task=task).oracle_result
        entry.update(
            verdict=result.verdict.value,
            status="judged",
            judge_detail=result.detail,
            native_map_sha256=file_sha256(native_map),
            captures=inspected.get("captures", []),
            native_operation=built,
            distinct_inspector_process_started=True,
            scene_reopened_verified=(
                inspected_execution.status is ExecutionStatus.SUCCESS
                and inspected.get("status") == "inspected"
            ),
        )
        detail = json.loads(result.detail) if result.detail.startswith("{") else {}
        entry["defects"] = detail.get("defects", [])
        entry["unavailable"] = detail.get("unavailable", [])
        manifest.update(oracle_evaluated=True, judge_verdict=result.verdict.value)
        seal(folder, manifest, created=True, judged=result.verdict.value == "pass")
        write_json(folder / "judge-result.json", entry)
        return entry
    finally:
        if parent and content_snapshot(parent / "project") != prior:
            raise RuntimeError("previous_native_version_changed")


def archive_evidence(root: Path) -> Path:
    """Keep actual projects, native content, captures and logs; exclude venv/DDC/engine caches."""
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(
            part in {"host-venv", "__pycache__", "DerivedDataCache", "Intermediate", "Saved"}
            for part in relative.parts
        ):
            continue
        if not path.is_file() or path.name == "evidence-index.json":
            continue
        refuse_personal_project(path)
        entries.append(
            {"path": relative.as_posix(), "sha256": file_sha256(path), "bytes": path.stat().st_size}
        )
    if sum(e["bytes"] for e in entries) > 512_000_000:
        raise RuntimeError("evidence_archive_budget_exceeded_raw_files_preserved")
    write_json(
        root / "evidence-index.json", {"files": entries, "sha256_scope": "unmodified evidence files"}
    )
    target = root.with_suffix(".zip")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.write(root / entry["path"], entry["path"])
        archive.write(root / "evidence-index.json", "evidence-index.json")
    return target


def validate(root: Path, engine: Path, criteria_path: Path | None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "blocked",
        "engine_executed": False,
        "correction_demonstrated": False,
        "scope": "small native graphic scene; objective judgement only",
        "stages": [],
        "blocked_capabilities": BLOCKED_CAPABILITIES,
        "started_unix_s": time.time(),
        "code_revision": json.loads(
            (Path(__file__).resolve().parents[3] / "bundle-manifest.json").read_text()
        )["source_commit"],
    }
    try:
        # Read-only: never stop an existing editor, model or APEX process.
        check_external_workloads()
        capabilities = probe(engine)
        write_json(root / "environment-probe.json", capabilities)
        version = capabilities["unreal"].get("version") or {}
        if not capabilities["unreal"]["available"] or (
            version.get("MajorVersion"),
            version.get("MinorVersion"),
        ) != (5, 8):
            raise RuntimeError("actual_ue5_8_installation_required")
        if Path(capabilities["unreal"]["root"]).resolve() != engine.resolve():
            raise RuntimeError("unexpected_engine_fallback_refused")
        write_json(
            root / "hardware-before.json",
            {
                "status": "real",
                "platform": platform.platform(),
                "cpu_logical": psutil.cpu_count(),
                "ram_total_bytes": psutil.virtual_memory().total,
                "ram_available_bytes": psutil.virtual_memory().available,
                "gpu": sample_gpu(),
                "free_disk_bytes": shutil.disk_usage(root).free,
            },
        )
        if psutil.virtual_memory().available < 4 * 1024**3 or shutil.disk_usage(root).free < 8 * 1024**3:
            raise RuntimeError("prerequisite_4GiB_free_RAM_8GiB_free_disk_required")
        criteria_data = json.loads(criteria_path.read_text(encoding="utf-8-sig")) if criteria_path else {}
        level = criteria_data.pop("level", "prototype")
        criteria = SceneCriteria.for_level(level, **criteria_data)
        write_json(root / "criteria.json", asdict(criteria))
        fixture = (
            Path(__file__).resolve().parents[3] / "scripts/game_graphics/windows/accepted-scene-plan.json"
        )
        plan = ScenePlan.parse(json.loads(fixture.read_text()))
        plan = replace(plan, level=criteria.level)
        first = run_stage(root, "01-baseline", plan, criteria, capabilities)
        report["stages"].append(first)
        if first["verdict"] != "pass":
            report["reason"] = "baseline_not_qualified_no_fault_injection"
            return report
        parent = root / "01-baseline"
        plan = replace(plan, revision=2, parent_manifest_sha256=file_sha256(parent / "scene-contract.json"))
        fault = run_stage(
            root, "02-fault", plan, criteria, capabilities, parent=parent, operation="inject_road_pose"
        )
        report["stages"].append(fault)
        if (
            fault["verdict"] != "fail"
            or fault.get("unavailable")
            or [(d["id"], d.get("actor")) for d in fault.get("defects", [])] != [("pose", "road")]
        ):
            report["reason"] = "injected_road_fault_not_uniquely_demonstrated_no_blind_repair"
            return report
        # Exactly one targeted correction. A failure cannot trigger an open-ended or unrelated rewrite.
        write_json(
            root / "correction-request.json",
            {
                "judge": "UnrealGraphicsOracle via Tier0Judge",
                "origin_stage": "02-fault",
                "actor": "road",
                "defect": fault["defects"][0],
                "operation": "repair_road_pose",
                "attempt": 1,
                "user_attempt_budget": criteria.max_attempts_per_defect,
                "qualification_attempt_cap": 1,
            },
        )
        parent = root / "02-fault"
        plan = replace(plan, revision=3, parent_manifest_sha256=file_sha256(parent / "scene-contract.json"))
        fixed = run_stage(
            root, "03-corrected", plan, criteria, capabilities, parent=parent, operation="repair_road_pose"
        )
        report["stages"].append(fixed)
        stop = compare_defects(fault["defects"], fixed.get("defects", []))
        if fixed["verdict"] == "pass" and not stop:
            report.update(
                status="pass",
                reason="native_save_reopen_fault_and_targeted_correction_verified",
                correction_demonstrated=True,
            )
        else:
            report["reason"] = stop or "targeted_correction_unqualified_stop_without_further_attempts"
        return report
    except Exception as exc:
        report["reason"] = type(exc).__name__ + ": " + str(exc)
        return report
    finally:
        started = list(root.glob("*/**/*-started.json"))
        report["engine_executed"] = any(
            json.loads(p.read_text()).get("engine_process_started") is True for p in started
        )
        report["elapsed_s"] = time.time() - report["started_unix_s"]
        report["gpu_after"] = sample_gpu()
        write_json(root / "validation-report.json", report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--criteria", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        print(
            json.dumps(
                {"status": "blocked", "reason": "Windows execution required", "engine_executed": False}
            )
        )
        return 2
    root = refuse_personal_project(args.run_root)
    if not root.name.startswith("APEX-UE58-") or not root.is_dir():
        raise ValueError("fresh_launcher_workspace_required")
    marker = json.loads((root / "launcher-owner.json").read_text(encoding="utf-8-sig"))
    if marker.get("kind") != "APEX-UE58-qualification" or marker.get("run_root") != str(root):
        raise ValueError("launcher_owner_mismatch")
    if (root / "validation-report.json").exists() or any(root.glob("0*-*")):
        raise ValueError("existing_qualification_run_refused")
    report = validate(root, args.engine_root, args.criteria)
    try:
        archive = archive_evidence(root)
        print(f"Evidence: {archive}\nSHA256: {file_sha256(archive)}", flush=True)
    except Exception as exc:
        print(f"Evidence files preserved at {root}; archive failed: {exc}", flush=True)
        return 2
    print(
        json.dumps(
            {k: report[k] for k in ("status", "reason", "engine_executed", "correction_demonstrated")}
        ),
        flush=True,
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
