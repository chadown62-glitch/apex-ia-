"""Own a real UE editor process, fixed trusted scripts and bounded resource monitoring."""

from __future__ import annotations

import contextlib
import csv
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from apex.game_graphics.models import file_sha256
from apex.game_graphics.ue.project import SCRIPTS, refuse_personal_project
from apex.obs.status import Measurement
from apex.sandbox.model import ExecutionStatus, IsolationLevel
from apex.sandbox.result import SandboxResult


def stop_owned(process: subprocess.Popen[bytes], descendants: list[psutil.Process] | None = None) -> None:
    if os.name == "nt":
        # psutil identities retain creation times, including after the editor has exited.
        children = list(descendants or [])
        if process.poll() is None:
            with contextlib.suppress(psutil.NoSuchProcess):
                children += psutil.Process(process.pid).children(recursive=True)
            process.terminate()
        for child in children:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.terminate()
        _, alive = psutil.wait_procs(children, timeout=5)
        for child in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
        psutil.wait_procs(alive, timeout=5)
    elif process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def sample_gpu() -> dict[str, Any]:
    """Real whole-device samples; neither editor-only VRAM nor renderer FPS."""
    executable = shutil.which("nvidia-smi")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/nvidia-smi.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        return {"status": "unknown", "reason": "nvidia_smi_unavailable"}
    try:
        result = subprocess.run(  # noqa: S603 - fixed read-only NVIDIA query
            [
                executable,
                "--query-gpu=uuid,name,driver_version,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        devices = [
            {
                "uuid": r[0].strip(),
                "name": r[1].strip(),
                "driver": r[2].strip(),
                "total_mib": float(r[3]),
                "used_mib": float(r[4]),
                "utilization_percent": float(r[5]),
            }
            for r in rows
        ]
        return {"status": "real", "scope": "whole_device", "devices": devices, "time_unix_s": time.time()}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        return {"status": "unknown", "reason": type(exc).__name__ + ": " + str(exc)}


def run_editor(
    capabilities: dict[str, Any],
    root: Path,
    mode: str,
    *,
    timeout_s: float = 600,
    max_sampled_rss_mb: float = 12000,
) -> tuple[SandboxResult, dict[str, Any]]:
    if mode not in ("build", "inspect", "mutate"):
        raise ValueError("unsupported_editor_operation")
    root = refuse_personal_project(root)
    editor = capabilities["unreal"].get("editor")
    if not capabilities["unreal"].get("available") or not editor or not Path(editor).is_file():
        raise RuntimeError("unreal_editor_missing_no_execution")
    with Path(editor).open("rb") as binary:
        magic = binary.read(4)
    if magic != b"\x7fELF" and magic[:2] != b"MZ":
        raise RuntimeError("native_unreal_editor_binary_required")
    version = capabilities["unreal"].get("version") or {}
    if version.get("MajorVersion") != 5 or version.get("MinorVersion", 0) < 6:
        raise RuntimeError("candidate_editor_adapter_requires_ue5_6_or_later")
    project = root / "project"
    source_name = {
        "build": "ue_build_scene.py",
        "inspect": "ue_inspect_scene.py",
        "mutate": "ue_mutate_scene.py",
    }[mode]
    script = project / "Tools" / source_name
    if file_sha256(script) != file_sha256(SCRIPTS / source_name):
        raise RuntimeError("editor_tool_script_changed")
    if mode == "mutate" and file_sha256(project / "Tools/ue_build_scene.py") != file_sha256(
        SCRIPTS / "ue_build_scene.py"
    ):
        raise RuntimeError("mutation_job_validator_changed")
    validator = Path(__file__).parent / "contract.py"
    if file_sha256(project / "Tools/scene_contract.py") != file_sha256(validator):
        raise RuntimeError("editor_contract_validator_changed")
    if any(c in str(script) for c in ('"', "\n", "\r", ";")):
        raise ValueError("unsafe_editor_script_path")
    marker = json.loads((project / ".apex-graphics-project.json").read_text())
    if file_sha256(project / "Requests/scene-plan.json") != marker["plan_sha256"]:
        raise RuntimeError("editor_plan_changed")
    job_path = project / "Requests" / (mode + "-job.json")
    job = json.loads(job_path.read_text())
    if job["nonce"] != marker["nonce"] or job["mode"] != mode:
        raise RuntimeError("editor_job_changed")
    report = root / (mode + "-observation.json")
    if report.exists():
        raise RuntimeError("stale_editor_receipt_use_new_revision")
    if mode == "inspect" and (root / "captures").exists():
        raise RuntimeError("stale_capture_directory_use_new_revision")
    log_path = root / (mode + "-editor.log")
    native_log = root / (mode + "-native.log")
    environment = dict(os.environ)
    environment["APEX_GRAPHICS_UE_JOB"] = str(job_path)
    argv = [
        str(editor),
        str(project / "ApexGraphics.uproject"),
        "-unattended",
        "-nosplash",
        "-NoSound",
        "-NoSourceControl",
        "-stdout",
        "-FullStdOutLogOutput",
        "-abslog=" + str(native_log),
        "-ddc=NoShared",
        '-ExecCmds=py "' + str(script) + '"',
    ]
    started = time.monotonic()
    peak_rss = 0
    rss_sample_times: list[float] = []
    descendants: dict[int, psutil.Process] = {}
    gpu_samples = [sample_gpu()]
    last_gpu_sample = time.monotonic()
    status = ExecutionStatus.SUCCESS
    with log_path.open("wb") as log:
        process = subprocess.Popen(  # noqa: S603 - native editor and fixed verified script, no shell
            argv, stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=(os.name != "nt")
        )
        (root / (mode + "-started.json")).write_text(
            json.dumps(
                {
                    "pid": process.pid,
                    "nonce": marker["nonce"],
                    "engine_process_started": True,
                    "process_stopped_verified": False,
                }
            )
        )
        try:
            while process.poll() is None:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    descendants.update({p.pid: p for p in children})
                    peak_rss = max(
                        peak_rss,
                        sum(p.memory_info().rss for p in [parent, *children]),
                    )
                    rss_sample_times.append(time.monotonic() - started)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                if time.monotonic() - last_gpu_sample >= 2:
                    gpu_samples.append(sample_gpu())
                    last_gpu_sample = time.monotonic()
                if peak_rss / (1024 * 1024) > max_sampled_rss_mb:
                    # Application sampling limit, not a kernel OOM claim.
                    status = ExecutionStatus.FAILED
                    break
                if time.monotonic() - started > timeout_s:
                    status = ExecutionStatus.TIMEOUT
                    break
                time.sleep(0.2)
        finally:
            stop_owned(process, list(descendants.values()))
            (root / (mode + "-started.json")).write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "nonce": marker["nonce"],
                        "engine_process_started": True,
                        "process_stopped_verified": process.poll() is not None,
                    }
                )
            )
    if status is ExecutionStatus.SUCCESS and process.returncode != 0:
        status = ExecutionStatus.FAILED
    elapsed = time.monotonic() - started
    gpu_samples.append(sample_gpu())
    (root / (mode + "-gpu-samples.json")).write_text(json.dumps(gpu_samples, indent=2))
    children_alive = []
    for child in descendants.values():
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                children_alive.append(child.pid)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            children_alive.append(child.pid)  # Unknown ownership state cannot be declared stopped.
    if children_alive:
        status = ExecutionStatus.FAILED
    critical_errors: list[str] = []
    for source in (log_path, native_log):
        if not source.is_file():
            continue
        with source.open(errors="replace") as lines:
            for line in lines:
                if any(
                    s in line
                    for s in (
                        "LogPython: Error:",
                        "LogMaterial: Error:",
                        "LogShaderCompilers: Error:",
                        "Fatal error:",
                        "LogLinker: Error:",
                    )
                ):
                    critical_errors.append(line.strip()[:1000])
                if len(critical_errors) >= 20:
                    break
    with log_path.open("rb") as tail:
        tail.seek(max(0, log_path.stat().st_size - 65536))
        text = tail.read().decode(errors="replace")
    facts = {
        "tool": "real_unreal_editor",
        "mode": mode,
        "pid": process.pid,
        "process_stopped_verified": process.poll() is not None,
        "observed_descendants_stopped": not children_alive,
        "observed_descendant_pids": sorted(descendants),
        "unobserved_descendants_excluded": False,
        "script_sha256": file_sha256(script),
        "validator_sha256": file_sha256(validator),
        "nonce": marker["nonce"],
        "plan_sha256": marker["plan_sha256"],
        "sampled_peak_tree_rss_bytes": peak_rss,
        "nominal_poll_interval_s": 0.2,
        "rss_sample_elapsed_times_s": rss_sample_times,
        "rss_is_exact_peak": False,
        "gpu_peak_mb": None,
        "isolation": "trusted local graphical editor, no OS sandbox",
        "no_null_rhi": True,
        "raw_log": str(log_path),
        "native_log": str(native_log),
        "gpu_samples_file": str(root / (mode + "-gpu-samples.json")),
        "model_code_executed": False,
    }
    facts["critical_engine_errors"] = critical_errors
    result = SandboxResult(
        mission_id=marker["scene_id"],
        clone_id="owned-unreal-graphics-editor",
        status=status,
        isolation_level=IsolationLevel.NONE,
        exit_code=Measurement.real(process.returncode),
        signal=Measurement.unknown(),
        elapsed_s=Measurement.real(elapsed),
        cpu_time_s=Measurement.unknown(),
        memory_peak_mb=Measurement.unknown(),
        pids_peak=Measurement.unknown(),
        stdout=text,
        stderr="",
        stdout_truncated=log_path.stat().st_size > 65536,
        stderr_truncated=False,
        facts=facts,
    )
    try:
        observation = json.loads(report.read_text()) if report.is_file() else {}
    except (OSError, ValueError):
        observation = {"status": "error", "error": "invalid_native_observation_json"}
    if observation and (
        observation.get("nonce") != marker["nonce"]
        or observation.get("plan_sha256") != marker["plan_sha256"]
    ):
        observation = {"status": "error", "error": "unbound_editor_receipt"}
    (root / (mode + "-execution.json")).write_text(json.dumps(result.to_dict(), indent=2, default=str))
    return result, observation
