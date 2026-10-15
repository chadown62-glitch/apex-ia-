"""Bounded trusted Blender processes and catalog plans through the existing Tool Engine."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import psutil

from apex.game_graphics.models import file_sha256
from apex.game_graphics.studio.contract import parse_plan
from apex.obs.status import Measurement
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult
from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry
from apex.tools.model import Permission, ToolCall, ToolSpec
from apex.tools.workspace import Workspace

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts/game_graphics/studio"
VIEWS = ("front_three_quarter", "rear_three_quarter", "side", "front")


def refuse_personal_project(path: Path):
    normalized = str(path.resolve()).replace("\\", "/").casefold()
    if "c:/gameproject" in normalized or normalized.startswith("/mnt/c/gameproject"):
        raise ValueError("personal_game_project_protected")


def store_plan(plan: dict, directory: Path, *, caller: str = "studio-controller") -> Path:
    """ToolEngine permissions gate data writing, never mislabel a native process as sandboxed."""
    directory.mkdir(parents=True, exist_ok=False)
    workspace = Workspace(directory, workspace_id="studio-plan")
    registry = ToolRegistry()

    def write(arguments, context, spec):
        validated = parse_plan(arguments)
        context.workspace.write_bytes("plan.json", json.dumps(validated, indent=2).encode())
        return {"validated": True, "operations": len(validated["operations"])}

    registry.register(
        ToolSpec(
            name="graphics.store_plan",
            description="Validate a bounded catalog plan and save JSON data.",
            input_schema={
                "type": "object",
                "properties": {
                    "family": {"type": "string"},
                    "operations": {"type": "array", "maxItems": 8},
                },
                "required": ["family", "operations"],
            },
            output_schema={"type": "object"},
            permissions=frozenset({Permission.WRITE_WORKSPACE}),
        ),
        write,
    )
    result = ToolEngine(registry).execute(
        ToolCall("graphics.store_plan", plan, "store-artistic-plan", caller),
        ToolContext(workspace, frozenset({Permission.WRITE_WORKSPACE})),
    )
    (directory / "tool-result.json").write_text(json.dumps(result.to_dict(), indent=2))
    if not result.ok:
        raise ValueError("artist_plan_refused:" + str(result.error))
    return directory / "plan.json"


def run_blender(script: str, arguments: list[str], log: Path, *, timeout_s=600, rss_limit_mib=12000,
                cancel_event=None):
    if script not in ("render_asset.py", "inspect_asset.py"):
        raise ValueError("untrusted_script")
    executable = shutil.which("blender")
    if not executable:
        raise RuntimeError("blender_missing")
    log.parent.mkdir(parents=True, exist_ok=True)
    sources = {p.name: file_sha256(p) for p in SCRIPTS.glob("*.py")}
    started = time.monotonic()
    samples, peak, cpu, pids_peak = [], 0, 0, 0
    reason = None
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--threads",
        "4",
        "--python-exit-code",
        "2",
        "--python",
        str(SCRIPTS / script),
        "--",
        *arguments,
    ]
    with log.open("w") as handle:
        child = subprocess.Popen(  # noqa: S603 - trusted fixed script, encoded arguments, no shell
            command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=os.name != "nt"
        )
        try:
            process = psutil.Process(child.pid)
            while child.poll() is None:
                elapsed = time.monotonic() - started
                try:
                    tree = [process, *process.children(recursive=True)]
                    rss = sum(p.memory_info().rss for p in tree if p.is_running())
                    cpu = max(cpu, sum(sum(p.cpu_times()[:2]) for p in tree if p.is_running()))
                    pids_peak = max(pids_peak, len(tree))
                    peak = max(peak, rss)
                    samples.append({"elapsed_s": round(elapsed, 3), "rss_bytes": rss})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                if cancel_event is not None and cancel_event.is_set():
                    reason = "cancelled"
                    break
                if elapsed > timeout_s or peak > rss_limit_mib * 1024**2:
                    reason = "timeout" if elapsed > timeout_s else "sampled_memory_limit"
                    break
                time.sleep(0.25)
        finally:
            if child.poll() is None:
                if os.name == "nt":
                    owned = psutil.Process(child.pid)
                    tree = [*owned.children(recursive=True), owned]
                    for item in tree:
                        try:
                            item.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, survivors = psutil.wait_procs(tree, timeout=5)
                    for item in survivors:
                        try:
                            item.kill()
                        except psutil.NoSuchProcess:
                            pass
                else:
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        child.kill()
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
    elapsed = time.monotonic() - started
    text = log.read_text(errors="replace")
    status = ExecutionStatus.SUCCESS if child.returncode == 0 and reason is None else ExecutionStatus.FAILED
    if reason == "timeout":
        status = ExecutionStatus.TIMEOUT
    result = SandboxResult(
        mission_id="game-graphics-studio",
        clone_id="trusted-catalog-worker",
        status=status,
        isolation_level=IsolationLevel.NONE,
        exit_code=Measurement.real(child.returncode),
        signal=Measurement.unknown(),
        elapsed_s=Measurement.real(elapsed),
        cpu_time_s=Measurement.real(cpu),
        memory_peak_mb=Measurement.real(peak / 1024**2),
        pids_peak=Measurement.real(pids_peak),
        stdout=text[-65536:],
        stderr="",
        stdout_truncated=len(text) > 65536,
        stderr_truncated=False,
        error_detail=reason,
        facts={
            "command": command,
            "pid": child.pid,
            "process_stopped_verified": child.poll() is not None,
            "execution": "trusted_local_catalog",
            "model_code_executed": False,
            "script_sha256": sources[script],
            "rss_kind": "sampled process tree RSS, not an OS-enforced memory limit",
            "sample_interval_s": 0.25,
            "timeout_s": timeout_s,
            "rss_limit_mib": rss_limit_mib,
            "sources": sources,
        },
    )
    log.with_suffix(".resources.json").write_text(
        json.dumps({"execution": result.to_dict(), "rss_samples": samples}, indent=2)
    )
    return result


def create_revision(root: Path, family: str, *, parent: Path | None = None, plan: dict | None = None,
                    cancel_event=None, timeout_s=600, rss_limit_mib=12000):
    refuse_personal_project(root)
    if parent:
        refuse_personal_project(parent)
    if root.exists():
        raise ValueError("revision_directory_must_be_new")
    root.mkdir(parents=True)
    args = ["--family", family, "--output", str(root.resolve())]
    parent_sha = file_sha256(parent) if parent else None
    if parent:
        if parent.is_symlink() or not parent.is_file():
            raise ValueError("parent_must_be_regular_blend")
        args += ["--input", str(parent.resolve()), "--parent-sha", parent_sha]
    if plan:
        if plan.get("family") != family:
            raise ValueError("plan_family_mismatch")
        plan_file = store_plan(plan, root / "plan")
        args += ["--plan", str(plan_file.resolve())]
    result = run_blender("render_asset.py", args, root / "build.log", cancel_event=cancel_event,
                         timeout_s=timeout_s, rss_limit_mib=rss_limit_mib)
    if parent and file_sha256(parent) != parent_sha:
        raise ValueError("parent_modified_during_execution")
    (root / "execution.json").write_text(json.dumps(result.to_dict(), indent=2))
    (root / "provenance.json").write_text(
        json.dumps(
            {
                "family": family,
                "method": "trusted_procedural_blender",
                "neural_geometry": False,
                "parent_sha256": parent_sha,
                "plan_sha256": file_sha256(root / "plan/plan.json") if plan else None,
                "source_scripts": result.facts["sources"],
            },
            indent=2,
        )
    )
    return result


def inspect_revision(root: Path, *, cancel_event=None):
    runs = []
    for ext in ("blend", "glb"):
        report = root / (ext + "-inspection.json")
        result = run_blender(
            "inspect_asset.py",
            ["--input", str((root / ("asset." + ext)).resolve()), "--report", str(report.resolve())],
            root / (ext + "-inspection.log"),
            timeout_s=120,
            cancel_event=cancel_event,
        )
        runs.append(result)
    return runs


def manifest_revision(root: Path, family: str, case_id: str):
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in ("manifest.json", "judge-report.json", "verdict.json"):
            continue
        name = path.relative_to(root).as_posix()
        role = "evidence"
        item = {"path": name, "sha256": file_sha256(path), "bytes": path.stat().st_size}
        if path.stem in VIEWS and path.suffix == ".png":
            role = "view"
            item["view"] = path.stem
        elif path.stem.startswith("silhouette_"):
            role = "silhouette"
            item["view"] = path.stem[11:]
        elif path.suffix == ".blend":
            role = "blender_project"
        elif path.suffix == ".glb":
            role = "exchange"
        item["role"] = role
        files.append(item)
    manifest = {
        "schema_version": 1,
        "family": family,
        "case_id": case_id,
        "units": "m",
        "files": files,
        "geometry_method": "procedural_blender",
        "pbr_texture_baking": False,
        "limits": ["procedural shaders are not baked into GLB", "Unreal import not executed"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
