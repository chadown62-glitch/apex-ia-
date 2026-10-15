"""Run the real authenticated garage recipe in an isolated cloud profile.

Requires an already installed local coder manifest and Blender. Does not
download weights or claim Unreal/CUDA qualification.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--description",
        default=(
            "Crée un petit garage réaliste avec voiture sportive, bâtiment et mobilier "
            "de décor, matériaux PBR et quatre rendus par asset."
        ),
    )
    options = parser.parse_args()
    output = options.output.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from apex.game_graphics.studio.execution import refuse_personal_project

    refuse_personal_project(output)
    output.mkdir(parents=True, exist_ok=True)
    if not options.manifest.is_file():
        parser.error("local coder manifest missing; no download attempted")
    os.environ["APEX_HOME"] = str(output)
    os.environ["APEX_DATA_ROOT"] = str(output / "data")
    os.environ["APEX_MODELS_DIR"] = str(output / "models")
    os.environ["APEX_DATABASE_URL"] = "sqlite:///" + (output / "apex.db").as_posix()
    os.environ["APEX_GAME_STUDIO_MODEL_MANIFEST"] = str(options.manifest.resolve())
    os.environ["APEX_API_TOKEN"] = "demo-" + secrets.token_urlsafe(24)
    os.environ["APEX_CONFIG"] = str(Path(__file__).resolve().parents[2] / "config.standalone.yaml")

    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.db.engine import build_engine
    from apex.db.models import Base

    Base.metadata.create_all(build_engine(os.environ["APEX_DATABASE_URL"]))
    headers = {"Authorization": "Bearer " + os.environ["APEX_API_TOKEN"]}
    request = {
        "creation_type": "environnement",
        "style": "realiste",
        "quality": "prototype",
        "tool": "blender",
        "compute_backend": "cpu",
        "controller_model": "pinned_7b",
        "description": options.description,
        "judges_all_tasks": True,
        "automatic_corrections": True,
        "max_time_s": 3300,
        "max_rss_mib": 12000,
        "max_attempts": 3,
        "criteria": {},
        "references": [],
    }
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/game-studio/projects", json=request, headers=headers)
        response.raise_for_status()
        project_id = response.json()["data"]["id"]
        (output / "project-id.txt").write_text(project_id)
        started = time.monotonic()
        while True:
            response = client.get(f"/api/v1/game-studio/projects/{project_id}", headers=headers)
            response.raise_for_status()
            project = response.json()["data"]
            (output / "api-project.json").write_text(json.dumps(project, indent=2, ensure_ascii=False))
            step_status = [(step["key"], step["status"]) for step in project["steps"]]
            print(project["status"], step_status, flush=True)
            if project["status"] in ("review", "failed", "cancelled"):
                break
            if time.monotonic() - started > request["max_time_s"] + 120:
                raise TimeoutError("project did not stop within requested budget")
            time.sleep(15)
    return 0 if project["status"] == "review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
