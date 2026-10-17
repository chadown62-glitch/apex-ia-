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
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil

REQUEST = {
    "creation_type": "vehicule",
    "style": "realiste",
    "quality": "prototype",
    "tool": "blender",
    "compute_backend": "cpu",
    "controller_model": "pinned_7b",
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
                deadline = time.monotonic() + REQUEST["max_time_s"] + 120
                while project["status"] in ("queued", "running") and time.monotonic() < deadline:
                    time.sleep(2)
                    response = client.get(f"/api/v1/game-studio/projects/{project_id}")
                    response.raise_for_status()
                    project = response.json()["data"]
                    save(output / "api-project.json", project)
                if project["status"] in ("queued", "running"):
                    client.post(f"/api/v1/game-studio/projects/{project_id}/cancel").raise_for_status()
                    raise RuntimeError("api_mission_qualification_timeout")
                downloads = output / "api-artifacts"
                downloads.mkdir()
                collected = []
                for step in project["steps"]:
                    child = step.get("mission")
                    if not child:
                        continue
                    for artifact in child.get("artifacts", []):
                        digest = artifact["hash"]
                        result = client.get(
                            f"/api/v1/game-studio/missions/{child['id']}/artifacts/{digest}"
                        )
                        result.raise_for_status()
                        if "sha256:" + hashlib.sha256(result.content).hexdigest() != digest:
                            raise RuntimeError("api_artifact_hash_mismatch")
                        (downloads / digest.removeprefix("sha256:")).write_bytes(result.content)
                        collected.append(artifact)
                return {
                    "status": project["status"],
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
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
        report["fixture"] = blender_fixture(output)
        report["mission"] = api_mission(output)
        report["status"] = (
            "COMPONENTS_TESTED" if report["mission"]["status"] == "BLOCKED" else report["mission"]["status"]
        )
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
