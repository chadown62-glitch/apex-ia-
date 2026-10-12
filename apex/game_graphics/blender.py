"""Execute trusted Blender tools and report their actual process outcomes."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from apex.game_graphics.contract import VehicleSpec
from apex.game_graphics.models import file_sha256
from apex.obs.status import Measurement
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "game_graphics"


def run_blender(script: str, args: list[str], log: Path, timeout_s: float = 300) -> SandboxResult:
    if script not in ("build_vehicle.py", "inspect_vehicle.py"):
        raise ValueError("untrusted_tool_script")
    executable = shutil.which("blender")
    if executable is None:
        raise RuntimeError("blender_missing")
    start = time.monotonic()
    try:
        process = subprocess.run(  # noqa: S603 - fixed trusted tool script, encoded paths, no shell
            [
                executable,
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--threads",
                "4",
                "--python",
                str(SCRIPTS / script),
                "--",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout, stderr, code = process.stdout, process.stderr, process.returncode
        status = ExecutionStatus.SUCCESS if code == 0 else ExecutionStatus.FAILED
    except subprocess.TimeoutExpired as exc:
        stdout = (
            (exc.stdout or b"").decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else exc.stdout or ""
        )
        stderr, code, status = "Blender timeout", None, ExecutionStatus.TIMEOUT
    elapsed = time.monotonic() - start
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(stdout + "\n" + stderr)
    return SandboxResult(
        mission_id="game-graphics",
        clone_id="trusted-blender-tool",
        status=status,
        isolation_level=IsolationLevel.NONE,
        exit_code=Measurement.real(code) if code is not None else Measurement.unknown(),
        signal=Measurement.unknown(),
        elapsed_s=Measurement.real(elapsed),
        cpu_time_s=Measurement.unknown(),
        memory_peak_mb=Measurement.unknown(),
        pids_peak=Measurement.unknown(),
        stdout=stdout[-65536:],
        stderr=stderr[-65536:],
        stdout_truncated=len(stdout) > 65536,
        stderr_truncated=len(stderr) > 65536,
        facts={
            "tool": "blender",
            "execution": "trusted_local_script",
            "model_code_executed": False,
            "script_sha256": file_sha256(SCRIPTS / script),
            "timeout_s": timeout_s,
        },
    )


def build_vehicle(spec: VehicleSpec, output: Path, *, defect: str = "none") -> SandboxResult:
    output.mkdir(parents=True, exist_ok=True)
    spec_file = output / "spec.json"
    spec_file.write_text(json.dumps(spec.to_dict(), indent=2))
    return run_blender(
        "build_vehicle.py",
        ["--spec", str(spec_file), "--output", str(output), "--defect", defect],
        output / "build.log",
    )


def inspect_vehicle(path: Path, output: Path) -> tuple[SandboxResult, dict[str, Any]]:
    label = "lod1" if "lod1" in path.stem else path.suffix[1:]
    report = output / (label + "-inspection.json")
    result = run_blender(
        "inspect_vehicle.py",
        ["--input", str(path), "--report", str(report)],
        output / (label + "-inspection.log"),
        timeout_s=120,
    )
    return result, json.loads(
        report.read_text()
    ) if report.is_file() and result.status is ExecutionStatus.SUCCESS else {}


def manifest_vehicle(
    output: Path, spec: VehicleSpec, model_facts: dict[str, Any] | None = None
) -> dict[str, Any]:
    schema = SCRIPTS.parents[1] / "docs" / "game_graphics" / "vehicle-asset-v1.schema.json"
    shutil.copyfile(schema, output / schema.name)
    files = []
    for name, role in [
        ("vehicle.blend", "blender_project"),
        ("vehicle.glb", "unreal_exchange"),
        ("vehicle-lod1.glb", "lod"),
        ("vehicle-asset-v1.schema.json", "schema"),
        ("front_three_quarter.png", "view"),
        ("rear_three_quarter.png", "view"),
        ("side.png", "view"),
        ("front.png", "view"),
        ("spec.json", "spec"),
    ]:
        path = output / name
        if not path.is_file():
            raise RuntimeError("artifact_missing:" + name)
        files.append(
            {"path": name, "role": role, "sha256": file_sha256(path), "bytes": path.stat().st_size}
        )
    wheels = []
    for front, side, name in [
        (1, -1, "wheel_front_left"),
        (1, 1, "wheel_front_right"),
        (-1, -1, "wheel_rear_left"),
        (-1, 1, "wheel_rear_right"),
    ]:
        wheels.append(
            {
                "object": name,
                "center_m": [
                    front * spec.wheelbase_m / 2,
                    side * (spec.width_m / 2 - spec.wheel_width_m / 2),
                    spec.wheel_radius_m,
                ],
                "radius_m": spec.wheel_radius_m,
                "spin_axis_local": [0, 0, 1],
                "spin_axis_blender_world": [0, 1, 0],
            }
        )
    manifest = {
        "schema_version": 1,
        "contract": "VehicleAssetContract",
        "producer": "apex.game_graphics",
        "geometry_method": "procedural_blender",
        "spec": spec.to_dict(),
        "units": "m",
        "blender_axes": {"forward": "+X", "up": "+Z", "handedness": "right"},
        "glb_axes": {"up": "+Y", "handedness": "right", "exporter_converted": True},
        "unreal_target": {
            "units": "cm",
            "up": "+Z",
            "forward": "+X",
            "scale_conversion": 100,
            "import_tested": False,
        },
        "wheels": wheels,
        "files": files,
        "controller": model_facts,
        "rig": {"kind": "separate_static_meshes", "skeletal_rig": False, "physics_asset": False},
        "lods": [{"level": 1, "path": "vehicle-lod1.glb", "method": "Blender decimate 0.5"}],
        "texture_maps": [],
        "scene_materials": "Principled PBR",
        "uv_present": True,
        "visual_judge_calibrated": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
