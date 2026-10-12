"""Own a real UE editor process, fixed trusted scripts and bounded resource monitoring."""

from __future__ import annotations

import contextlib
import json
import os
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


def stop_owned(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Stop only descendants observed under our process, never global editors by name.
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        process.terminate()
        for child in children:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.terminate()
        _, alive = psutil.wait_procs(children, timeout=5)
        for child in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def run_editor(
    capabilities: dict[str, Any],
    root: Path,
    mode: str,
    *,
    timeout_s: float = 600,
    max_sampled_rss_mb: float = 12000,
) -> tuple[SandboxResult, dict[str, Any]]:
    if mode not in ("build", "inspect"):
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
    source_name = "ue_build_scene.py" if mode == "build" else "ue_inspect_scene.py"
    script = project / "Tools" / source_name
    if file_sha256(script) != file_sha256(SCRIPTS / source_name):
        raise RuntimeError("editor_tool_script_changed")
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
    log_path = root / (mode + "-editor.log")
    environment = dict(os.environ)
    environment["APEX_GRAPHICS_UE_JOB"] = str(job_path)
    argv = [
        str(editor),
        str(project / "ApexGraphics.uproject"),
        "-unattended",
        "-nosplash",
        "-NoSound",
        "-NoSourceControl",
        '-ExecCmds=py "' + str(script) + '"',
    ]
    started = time.monotonic()
    peak_rss = 0
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
                    peak_rss = max(
                        peak_rss,
                        sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)]),
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                if peak_rss / (1024 * 1024) > max_sampled_rss_mb:
                    # Application sampling limit, not a kernel OOM claim.
                    status = ExecutionStatus.FAILED
                    break
                if time.monotonic() - started > timeout_s:
                    status = ExecutionStatus.TIMEOUT
                    break
                time.sleep(0.2)
        finally:
            stop_owned(process)
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
    critical_errors: list[str] = []
    with log_path.open(errors="replace") as logged:
        for line in logged:
            if (
                any(
                    s in line
                    for s in (
                        "LogPython: Error:",
                        "LogMaterial: Error:",
                        "LogShaderCompilers: Error:",
                        "Fatal error:",
                        "LogLinker: Error:",
                    )
                )
                and len(critical_errors) < 20
            ):
                critical_errors.append(line.strip()[:1000])
    with log_path.open("rb") as tail:
        tail.seek(max(0, log_path.stat().st_size - 65536))
        text = tail.read().decode(errors="replace")
    facts = {
        "tool": "real_unreal_editor",
        "mode": mode,
        "pid": process.pid,
        "process_stopped_verified": process.poll() is not None,
        "script_sha256": file_sha256(script),
        "validator_sha256": file_sha256(validator),
        "nonce": marker["nonce"],
        "plan_sha256": marker["plan_sha256"],
        "sampled_peak_tree_rss_bytes": peak_rss,
        "rss_sampling_interval_s": 0.2,
        "rss_is_exact_peak": False,
        "gpu_peak_mb": None,
        "isolation": "trusted local graphical editor, no OS sandbox",
        "no_null_rhi": True,
        "raw_log": str(log_path),
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
