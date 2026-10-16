"""Frozen-case qualification through authenticated APEX, real local Qwen/Blender.

No weights are downloaded. CPU requires --cpu-explicit on this cloud; CUDA is
otherwise the default and failures are retained, never silently substituted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cpu-explicit", action="store_true")
    parser.add_argument("--only", help="Optional comma-separated case IDs")
    parser.add_argument("--execute", action="store_true", help="Also execute the marked multi-asset case")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from apex.game_graphics.studio.execution import refuse_personal_project

    output = args.output.resolve()
    refuse_personal_project(output)
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        APEX_HOME=str(output),
        APEX_DATA_ROOT=str(output / "data"),
        APEX_MODELS_DIR=str(output / "models"),
        APEX_DATABASE_URL="sqlite:///" + str(output / "apex.db"),
        APEX_GAME_STUDIO_MODEL_MANIFEST=str(args.manifest.resolve()),
        APEX_API_TOKEN=secrets.token_urlsafe(24),
        APEX_CONFIG=str(repo / "config.standalone.yaml"),
    )
    import psutil
    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.db.engine import build_engine
    from apex.db.models import Base
    from apex.game_studio.capabilities import probe

    Base.metadata.create_all(build_engine(os.environ["APEX_DATABASE_URL"]))
    benchmark = json.loads((repo / "docs/game_studio/candidate/benchmark.json").read_text())
    (output / "benchmark.json").write_text(json.dumps(benchmark, indent=2))
    (output / "host.json").write_text(
        json.dumps(
            {
                "cpu_count": os.cpu_count(),
                "ram_bytes": psutil.virtual_memory().total,
                "capabilities": probe(),
            },
            indent=2,
        )
    )
    headers = {"Authorization": "Bearer " + os.environ["APEX_API_TOKEN"]}
    results = []
    with TestClient(create_app()) as client:
        for case in benchmark["cases"]:
            if args.only and case["id"] not in args.only.split(","):
                continue
            trials = list(range(1, benchmark["repetitions"] + 1)) + (
                [3] if case.get("execute") and args.execute else []
            )
            for trial in trials:
                started = time.monotonic()
                request = {
                    "creation_type": "environnement",
                    "style": case.get("style", "realiste"),
                    "quality": "prototype",
                    "tool": case.get("tool", "blender"),
                    "compute_backend": "cpu" if args.cpu_explicit else "cuda",
                    "controller_model": "pinned_7b",
                    "description": case["description"],
                    "judges_all_tasks": True,
                    "automatic_corrections": True,
                    "max_time_s": 3600,
                    "max_rss_mib": 12000,
                    "max_attempts": 3,
                    "plan_only": trial != 3,
                    "criteria": {},
                    "references": [],
                }
                response = client.post("/api/v1/game-studio/projects", json=request, headers=headers)
                row = {
                    "case": case["id"],
                    "trial": trial,
                    "request": request,
                    "http_status": response.status_code,
                }
                if response.status_code != 200:
                    row["failure"] = response.text
                else:
                    project_id = response.json()["data"]["id"]
                    last = None
                    while True:
                        project = client.get(
                            "/api/v1/game-studio/projects/" + project_id, headers=headers
                        ).json()["data"]
                        state = (project["status"], [(s["key"], s["status"]) for s in project["steps"]])
                        if state != last:
                            print(case["id"], trial, project_id, state, flush=True)
                            last = state
                        if project["status"] in ("review", "failed", "cancelled"):
                            # Wait for resource-accounting finalization, not just receipt publication.
                            from apex.game_studio.lifecycle import busy

                            if not busy(output / ".apex/game-studio/missions/projects" / project_id):
                                break
                        if time.monotonic() - started > 3720:
                            client.post(
                                "/api/v1/game-studio/projects/" + project_id + "/cancel", headers=headers
                            )
                            raise TimeoutError("worker_did_not_stop")
                        time.sleep(1)
                    row["project"] = project
                    if trial == 3:
                        for artifact in (project.get("assembly") or {}).get("artifacts", []):
                            data = client.get(
                                f"/api/v1/game-studio/projects/{project_id}/artifacts/{artifact['hash']}",
                                headers=headers,
                            )
                            data.raise_for_status()
                            assert hashlib.sha256(data.content).hexdigest() == artifact[
                                "hash"
                            ].removeprefix("sha256:")
                        row["assembly_downloads_verified"] = bool(project.get("assembly"))
                row["wall_s"] = time.monotonic() - started
                results.append(row)
                (output / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
                print("RESULT", case["id"], trial, row["wall_s"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
