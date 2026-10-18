"""Opt-in qualification of the installed runtime; never downloads models.

The fixed artistic fixture qualifies Blender only. A separate API request uses
real Qwen or records BLOCKED; it never substitutes the fixture for inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

REQUEST = {
    "creation_type": "vehicule",
    "style": "realiste",
    "quality": "prototype",
    "tool": "blender",
    "compute_backend": "cpu",
    "controller_model": "auto",
    "description": (
        "Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender."
    ),
    "judges_all_tasks": True,
    "automatic_corrections": True,
    "max_time_s": 3600,
    "max_rss_mib": 12000,
    "max_attempts": 3,
}
ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def budget_windows_contention(output):
    """Real Windows reader/writer contention, not an injected I/O exception."""
    if os.name != "nt":
        return {"status": "not_applicable", "reason": "native_Windows_required"}
    from apex.game_studio.lifecycle import RunBudget, atomic_json

    folder = output / "budget-contention"
    folder.mkdir()
    target, replacement = folder / "record.json", folder / "replacement.json"
    atomic_json(target, {"version": 1})
    atomic_json(replacement, {"version": 2})
    observed = None
    with target.open("rb"):
        try:
            os.replace(replacement, target)
        except OSError as exc:
            observed = {
                "error": type(exc).__name__,
                "winerror": getattr(exc, "winerror", None),
                "errno": exc.errno,
            }
    reader = target.open("rb")
    release = threading.Timer(0.2, reader.close)
    release.start()
    started = time.monotonic()
    try:
        atomic_json(target, {"version": 3})
    finally:
        release.join()
        reader.close()
    elapsed = time.monotonic() - started
    cancel = threading.Event()
    with RunBudget(folder, 10, cancel) as budget:
        with (folder / "budget.json").open("rb"):
            time.sleep(0.4)
        budget.checkpoint()
        if cancel.is_set():
            raise RuntimeError("transient_reader_cancelled_budget")
    report = {
        "original_replace_error": observed,
        "bounded_replace_elapsed_s": elapsed,
        "final_value": json.loads(target.read_bytes()),
        "budget_cancelled": cancel.is_set(),
        "status": "pass",
    }
    save(output / "budget-contention.json", report)
    return report


def collect_api_artifacts(client, project, downloads):
    """Download through the SAME ownership-checked endpoints used by the UI."""
    downloads.mkdir(exist_ok=True)
    sources = []
    base = f"/api/v1/game-studio/projects/{project['id']}/artifacts/"
    if project.get("planning_artifact"):
        sources.append((base, project["planning_artifact"]))
    for assembly in [project.get("assembly"), *project.get("assembly_history", [])]:
        sources.extend((base, a) for a in (assembly or {}).get("artifacts", []))
    for step in project.get("steps", []):
        sources.extend((base, a) for a in (step.get("handoff") or {}).get("artifacts", []))
        child = step.get("mission")
        if child:
            child_base = f"/api/v1/game-studio/missions/{child['id']}/artifacts/"
            for revision in child.get("revisions", []):
                sources.extend((child_base, a) for a in revision.get("artifacts", []))
            sources.extend((child_base, a) for a in child.get("controller_attempts", []))
    collected = []
    for base, artifact in sources:
        digest = artifact["hash"]
        result = client.get(base + digest)
        result.raise_for_status()
        if "sha256:" + hashlib.sha256(result.content).hexdigest() != digest:
            raise RuntimeError("api_artifact_hash_mismatch")
        # Never trust an artifact's filename as a local path.
        (downloads / digest.removeprefix("sha256:")).write_bytes(result.content)
        collected.append({**artifact, "api_path": base + digest})
    save(downloads / "index.json", collected)
    return collected


def verify_real_mission(project, downloads):
    """Fail closed: a review state or a Blender fixture alone proves no inference."""
    from apex.game_graphics.models import CODER_SHA256

    planner = project.get("planner_model") or {}
    planning = project.get("planning_artifact")
    checks = {
        "project_review": project.get("status") == "review",
        "pinned_planner": planner.get("sha256") == CODER_SHA256,
        "explicit_cpu": planner.get("device") == "cpu_explicit",
        "planner_stopped": planner.get("process_stopped_verified") is True,
    }
    trace = {}
    if planning:
        trace = json.loads((downloads / planning["hash"].removeprefix("sha256:")).read_bytes())
    checks["real_validated_response"] = bool(
        trace.get("accepted") is True
        and trace.get("status") == "validated"
        and trace.get("attempts")
        and trace["attempts"][-1].get("raw_response")
        and trace["attempts"][-1].get("generation", {}).get("model_used")
    )
    vehicles = [s for s in project.get("steps", []) if s.get("kind") == "vehicle"]
    checks["one_vehicle"] = len(vehicles) == 1
    checks["vehicle_preflight"] = bool(vehicles) and all(
        s.get("handoff", {}).get("status") == "pass_preflight" for s in vehicles
    )
    for step in vehicles:
        child = step.get("mission") or {}
        checks["child_review"] = child.get("status") == "review"
        revisions = child.get("revisions", [])
        checks["blender_files_and_four_views"] = any(
            {"asset.blend", "asset.glb"} <= {a["name"] for a in r.get("artifacts", [])}
            and sum(a["name"].endswith(".png") for a in r.get("artifacts", [])) >= 4
            and r.get("verdict") == "pass"
            for r in revisions
        )
    return {"passed": all(checks.values()), "checks": checks}


def api_lifecycle_probe(client, home, output):
    """Cancel a real Qwen load, resume the same project and plan a held-out request.

    Only called after a successful real vehicle mission. No injected diagnostic,
    model replacement or new provisioning path. No Blender generation in this probe.
    """
    from apex.game_studio.lifecycle import busy

    request = {
        **REQUEST,
        "description": "Crée deux fauteuils verts pour un coin lecture.",
        "creation_type": "objet_3d",
        "plan_only": True,
        "max_time_s": 1800,
    }
    response = client.post("/api/v1/game-studio/projects", json=request)
    response.raise_for_status()
    project = response.json()["data"]
    base = f"/api/v1/game-studio/projects/{project['id']}"
    folder = home / ".apex/game-studio/missions/projects" / project["id"]
    owner_file = folder / "planner-model/model.owner.json"
    deadline = time.monotonic() + 180
    while not owner_file.exists() and time.monotonic() < deadline:
        response = client.get(base)
        response.raise_for_status()
        project = response.json()["data"]
        if project["status"] not in ("queued", "running"):
            raise RuntimeError("planner_probe_failed_before_owned_server")
        time.sleep(0.1)
    if not owner_file.exists():
        client.post(base + "/cancel").raise_for_status()
        raise RuntimeError("planner_probe_no_owned_server")
    owner = json.loads(owner_file.read_bytes())
    client.post(base + "/cancel").raise_for_status()
    deadline = time.monotonic() + 30
    while busy(folder) and time.monotonic() < deadline:
        time.sleep(0.1)
    if busy(folder):
        raise RuntimeError("cancelled_planner_worker_still_owned")
    cancelled = client.get(base)
    cancelled.raise_for_status()
    cancelled = cancelled.json()["data"]
    save(output / "probe-cancelled.json", cancelled)
    save(output / "probe-cancelled-owner.json", json.loads(owner_file.read_bytes()))
    lifecycle = folder / "planner-model/model-lifecycle.json"
    if lifecycle.exists():
        shutil.copyfile(lifecycle, output / "probe-cancelled-lifecycle.json")
    if cancelled["status"] != "cancelled":
        raise RuntimeError("planner_probe_not_cancelled")
    try:
        process = psutil.Process(owner["pid"])
        if process.create_time() == owner["create_time"] and process.is_running():
            raise RuntimeError("cancelled_planner_orphan_present")
    except psutil.NoSuchProcess:
        pass
    response = client.post(base + "/resume")
    response.raise_for_status()
    resumed = response.json()["data"]
    deadline = time.monotonic() + request["max_time_s"] + 30
    while resumed["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(2)
        response = client.get(base)
        response.raise_for_status()
        resumed = response.json()["data"]
        save(output / "probe-resumed.json", resumed)
    if resumed["status"] in ("queued", "running"):
        client.post(base + "/cancel").raise_for_status()
        raise RuntimeError("planner_probe_resume_timeout")
    collect_api_artifacts(client, resumed, output / "probe-artifacts")
    checks = {
        "same_project": resumed["id"] == cancelled["id"],
        "new_mission_attempt": resumed["mission_id"] != cancelled["mission_id"],
        "cumulative_budget": resumed["budget"]["charged_s"] >= cancelled["budget"]["charged_s"] > 0,
        "plan_only_review": resumed["status"] == "review" and resumed.get("delivery_status") == "plan_only",
        "two_chairs": sum(s["kind"] == "chair" for s in resumed.get("steps", [])) == 2,
        "resumed_server_stopped": (resumed.get("planner_model") or {}).get("process_stopped_verified")
        is True,
    }
    report = {
        "request": request,
        "checks": checks,
        "passed": all(checks.values()),
        "scope": "real API cancellation during Qwen loading, resume and held-out plan; no chair meshes",
    }
    save(output / "probe-report.json", report)
    return report


def lease_fixture(output):
    """Exercise OS exclusion with a separate interpreter, never fake GPU memory."""
    from apex.game_graphics.resources import _locked
    from apex.game_studio.gpu import resource_lease

    shared = str(Path(os.environ["APEX_HOME"]) / ".apex/model-session.lock")
    code = (
        "import sys; from pathlib import Path; from apex.game_graphics.resources import _locked\n"
        "try:\n with _locked(Path(sys.argv[1])): pass\n"
        "except (BlockingIOError, PermissionError): raise SystemExit(73)\n"
    )
    command = [sys.executable, "-c", code, shared]
    with resource_lease(output / "lease-proof/resource.lock"):
        blocked = subprocess.run(command, capture_output=True, text=True, timeout=30)  # noqa: S603
    available = subprocess.run(command, capture_output=True, text=True, timeout=30)  # noqa: S603
    with _locked(Path(shared)), resource_lease(output / "cpu-proof/resource.lock", compute_backend="cpu"):
        gpu_still_held = subprocess.run(command, capture_output=True, text=True, timeout=30)  # noqa: S603
        workload_command = [*command[:-1], str(Path(shared).with_name("game-studio-workload.lock"))]
        cpu_excluded = subprocess.run(  # noqa: S603
            workload_command, capture_output=True, text=True, timeout=30
        )
    report = {
        "contender_while_held": blocked.returncode,
        "contender_after_release": available.returncode,
        "cpu_entered_while_gpu_lease_held": True,
        "cpu_did_not_release_gpu": gpu_still_held.returncode == 73,
        "cpu_workload_exclusion": cpu_excluded.returncode == 73,
        "stderr": [blocked.stderr, available.stderr],
        "cuda_qualified": False,
        "scope": "real separate-process OS lease exclusion, no GPU resident",
    }
    save(output / "lease-report.json", report)
    if (blocked.returncode, available.returncode, gpu_still_held.returncode, cpu_excluded.returncode) != (
        73,
        0,
        73,
        73,
    ):
        raise RuntimeError("native_shared_lease_exclusion_failed")
    return report


def blender_fixture(output):
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
    from apex.game_graphics.contract import QualityCriteria
    from apex.game_graphics.studio.benchmark import judge_revision
    from apex.game_graphics.studio.execution import create_revision, run_blender
    from apex.game_studio.vehicle_handoff import prepare_bundle
    from apex.sandbox.model import ExecutionStatus

    benchmark = json.loads((ROOT / "docs/game_graphics/studio/benchmark-v1.json").read_text())
    plan = {
        "family": "vehicle",
        "operations": [
            {"op": "inspect_scene", "target": "scene", "params": {}},
            {
                "op": "set_material_pbr",
                "target": "petrol_paint",
                "params": {"base_color": [0.65, 0.025, 0.015], "roughness": 0.24, "metallic": 0.78},
            },
            {"op": "save_revision_and_render", "target": "scene", "params": {}},
        ],
    }
    first = output / "blender-before"
    result = create_revision(first, "vehicle", plan=plan)
    if result.status is not ExecutionStatus.SUCCESS:
        raise RuntimeError("blender_fixture_failed:" + str(result.error_detail))
    criteria = QualityCriteria.for_level("prototype")
    judge = judge_revision(first, "vehicle", criteria, benchmark)
    before = prepare_bundle(
        first, output / "engineer-before", source_hash="sha256:" + sha(first / "asset.glb")
    )
    selected, after = first, before
    if before["verdict"] != "pass_preflight":
        selected = output / "blender-corrected"
        source_sha = sha(first / "asset.blend")
        repair = run_blender(
            "render_asset.py",
            [
                "--family",
                "vehicle",
                "--input",
                str(first / "asset.blend"),
                "--output",
                str(selected),
                "--contract-wheels",
                "--parent-sha",
                source_sha,
            ],
            output / "repair.log",
        )
        if repair.status is not ExecutionStatus.SUCCESS or sha(first / "asset.blend") != source_sha:
            raise RuntimeError("vehicle_repair_failed_or_parent_changed")
        save(selected / "execution.json", repair.to_dict())
        judge = judge_revision(selected, "vehicle", criteria, benchmark)
        after = prepare_bundle(
            selected, output / "engineer-after", source_hash="sha256:" + sha(selected / "asset.glb")
        )
    store = LocalFilesystemArtifactStore(root=str(output / "artifact-store"))
    stored = []
    for path in sorted(selected.iterdir()):
        if path.suffix in (".blend", ".glb", ".png", ".json"):
            data = path.read_bytes()
            metadata = store.put(data)
            if store.get(metadata.content_hash) != data:
                raise RuntimeError("artifact_roundtrip_mismatch")
            stored.append({"name": path.name, "hash": metadata.content_hash, "bytes": len(data)})
    report = {
        "method": "explicit_fixed_catalog_fixture_not_Qwen",
        "neural": False,
        "before": before,
        "after": after,
        "judge": judge,
        "artifacts": stored,
    }
    save(output / "blender-report.json", report)
    if after["verdict"] != "pass_preflight":
        raise RuntimeError("vehicle_preflight_still_failed")
    if judge["verdict"] != "pass":
        raise RuntimeError("blender_technical_judge_failed")
    return report


def api_mission(output):
    import httpx

    from apex.standalone import environment

    home = Path(os.environ["APEX_HOME"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, **environment(home, root=ROOT)}
    command = [sys.executable, "-m", "apex.standalone", "serve", "--port", str(port)]
    with (output / "api.log").open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(  # noqa: S603 - own runtime module, no shell
            command, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=ROOT
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError("api_start_failed_see_api_log")
                    token_path = home / ".apex/api-token"
                    if token_path.is_file():
                        try:
                            response = client.get("/api/v1/game-studio/capabilities")
                            if response.status_code == 401:
                                break
                        except httpx.TransportError:
                            pass
                    time.sleep(1)
                else:
                    raise RuntimeError("api_start_timeout")
                client.headers["Authorization"] = "Bearer " + token_path.read_text().strip()
                response = client.get("/api/v1/game-studio/capabilities")
                response.raise_for_status()
                save(output / "api-capabilities.json", response.json())
                frontend = client.get("/game-studio")
                frontend.raise_for_status()
                if "<html" not in frontend.text.lower():
                    raise RuntimeError("packaged_frontend_not_served")
                save(output / "api-request.json", REQUEST)
                response = client.post("/api/v1/game-studio/projects", json=REQUEST)
                save(
                    output / "api-create.json",
                    {"http_status": response.status_code, "body": response.json()},
                )
                if response.status_code == 409 and "local_planner_model_missing" in response.text:
                    return {
                        "status": "BLOCKED",
                        "reason": "verified_Qwen_weights_absent",
                        "api_auth": "unauthenticated_401_authenticated_200",
                        "frontend_served": True,
                        "inference_executed": False,
                    }
                response.raise_for_status()
                project = response.json()["data"]
                project_id = project["id"]
                save(output / "api-project.json", project)
                deadline = time.monotonic() + REQUEST["max_time_s"] + 120
                while project["status"] in ("queued", "running") and time.monotonic() < deadline:
                    time.sleep(2)
                    response = client.get(f"/api/v1/game-studio/projects/{project_id}")
                    response.raise_for_status()
                    project = response.json()["data"]
                    save(output / "api-project.json", project)
                    with (output / "api-progress.jsonl").open("a", encoding="utf-8") as progress:
                        progress.write(
                            json.dumps(
                                {
                                    "observed_at": time.time(),
                                    "status": project["status"],
                                    "events": project.get("events", []),
                                }
                            )
                            + "\n"
                        )
                if project["status"] in ("queued", "running"):
                    client.post(f"/api/v1/game-studio/projects/{project_id}/cancel").raise_for_status()
                    raise RuntimeError("api_mission_qualification_timeout")
                downloads = output / "api-artifacts"
                collected = collect_api_artifacts(client, project, downloads)
                validation = verify_real_mission(project, downloads)
                save(output / "api-validation.json", validation)
                lifecycle = api_lifecycle_probe(client, home, output) if validation["passed"] else None
                return {
                    "status": "review" if validation["passed"] and lifecycle["passed"] else "FAILED",
                    "mission_status": project["status"],
                    "validation": validation,
                    "lifecycle_probe": lifecycle,
                    "project_id": project_id,
                    "artifacts": collected,
                    "cpu_explicit": True,
                    "cuda_qualified": False,
                }
        finally:
            if process.poll() is None:
                subprocess.run(
                    [sys.executable, "-m", "apex.standalone", "stop", "--timeout", "15"],
                    env=env,
                    cwd=ROOT,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=40,
                )
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
            # Only logs/receipts from this fresh profile, never credentials or weights.
            studio = home / ".apex/game-studio"
            for section in ("missions", "projects"):
                for source in (studio / section).rglob("*"):
                    if source.is_file() and source.suffix in (".log", ".json"):
                        target = output / "runtime-evidence" / source.relative_to(studio)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-inference", action="store_true")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    from apex.game_graphics.studio.execution import refuse_personal_project

    refuse_personal_project(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "cpu_logical": psutil.cpu_count(),
        "ram_bytes": psutil.virtual_memory().total,
        "cuda_qualified": False,
        "user_windows11_qualified": False,
    }
    try:
        import llama_cpp
        import torch

        from apex.game_graphics.software import discover_blender

        report["cuda_available"] = torch.cuda.is_available()
        report["llama_cpp"] = {
            "version": llama_cpp.__version__,
            "library_loaded": True,
            "system_info": llama_cpp.llama_print_system_info().decode(errors="replace"),
        }
        cli = subprocess.run(
            [sys.executable, "-m", "llama_cpp.server", "--help"], capture_output=True, text=True, timeout=60
        )
        (output / "planner-server-help.log").write_text(cli.stdout + cli.stderr, encoding="utf-8")
        if cli.returncode:
            raise RuntimeError("planner_server_import_failed")
        report["llama_cpp"]["server_cli_imported"] = True
        blender = discover_blender()
        version = subprocess.run(  # noqa: S603 - validated Blender executable
            [blender["path"], "--version"], capture_output=True, text=True, timeout=30
        )
        version.check_returncode()
        report["blender"] = {**blender, "version": version.stdout}
        report["lease"] = lease_fixture(output)
        report["budget_contention"] = budget_windows_contention(output)
        report["fixture"] = (
            {"status": "not_run", "reason": "real_API_mission_required_instead; fixture_qualified_in_PR29"}
            if args.require_inference
            else blender_fixture(output)
        )
        report["mission"] = api_mission(output)
        report["status"] = (
            "COMPONENTS_TESTED" if report["mission"]["status"] == "BLOCKED" else report["mission"]["status"]
        )
        if args.require_inference and report["mission"]["status"] == "BLOCKED":
            report["status"] = "BLOCKED"
            return 2
        return 0 if report["mission"]["status"] in ("BLOCKED", "review") else 1
    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        report["elapsed_s"] = time.monotonic() - started
        save(output / "report.json", report)
        files = [
            {"path": p.relative_to(output).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size}
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name != "SHA256.json"
        ]
        save(output / "SHA256.json", files)


if __name__ == "__main__":
    raise SystemExit(main())
