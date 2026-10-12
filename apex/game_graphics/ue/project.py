"""New isolated graphics project, immutable plans and scene-parent/owner contracts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from apex.game_graphics.models import file_sha256
from apex.game_graphics.ue.contract import ScenePlan
from apex.tools.workspace import Workspace, WorkspaceLimits

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/game_graphics"
LIMITS = WorkspaceLimits(max_file_bytes=16_000_000, max_total_bytes=64_000_000, max_files=200)


def refuse_personal_project(path: Path) -> Path:
    for text in (str(path), str(path.resolve())):
        normal = text.replace("\\", "/").lower().replace("//?/", "").rstrip("/")
        if any(normal == p or normal.startswith(p + "/") for p in ("c:/gameproject", "/mnt/c/gameproject")):
            raise ValueError("protected_personal_project")
    if path.is_symlink() or len(path.resolve().parts) < 3:
        raise ValueError("isolated_graphics_project_required")
    return path.resolve()


def prepare(root: Path, plan: ScenePlan, *, parent: Path | None = None) -> dict[str, Any]:
    root = refuse_personal_project(root)
    if root.exists():
        raise ValueError("preserve_previous_output_use_new_root")
    if plan.revision > 1:
        if parent is None or not parent.is_file() or file_sha256(parent) != plan.parent_manifest_sha256:
            raise ValueError("scene_parent_receipt_unverified")
        previous = json.loads(parent.read_text())
        if previous["scene_id"] != plan.scene_id or previous["revision"] + 1 != plan.revision:
            raise ValueError("scene_revision_chain_invalid")
        if set(previous["protected_engineer_ids"]) != set(plan.protected_engineer_ids):
            raise ValueError("engineer_dependency_contract_changed")
        # Editing an integrated engineer level needs a qualified functional-dependency adapter.
        if previous.get("engineer_integrated"):
            raise ValueError("integrated_engineer_level_requires_functional_revalidation")
    elif parent is not None:
        raise ValueError("unexpected_parent_for_initial_scene")
    if plan.protected_engineer_ids:
        # Never rebuild a new map and silently omit protected functional actors.
        raise ValueError("protected_engineer_world_not_supported_by_first_demo")
    root.mkdir(parents=True)
    workspace = Workspace.create(root, "project", limits=LIMITS)
    plan_json = json.dumps(plan.to_dict(), indent=2).encode()
    workspace.write_bytes("Requests/scene-plan.json", plan_json)
    marker = {
        "kind": "APEX-isolated-graphics-scene",
        "nonce": str(uuid.uuid4()),
        "scene_id": plan.scene_id,
        "revision": plan.revision,
        "plan_sha256": file_sha256(workspace.root / "Requests/scene-plan.json"),
    }
    workspace.write_bytes(".apex-graphics-project.json", json.dumps(marker).encode())
    project = {
        "FileVersion": 3,
        "Category": "APEX graphics only",
        "Plugins": [
            {"Name": "PythonScriptPlugin", "Enabled": True},
            {"Name": "EditorScriptingUtilities", "Enabled": True},
        ],
    }
    workspace.write_bytes("ApexGraphics.uproject", json.dumps(project, indent=2).encode())
    # Keep default rendering modest. Lumen/Nanite are not silently enabled without qualification.
    workspace.write_bytes(
        "Config/DefaultEngine.ini",
        b"[/Script/Engine.RendererSettings]\nr.DynamicGlobalIlluminationMethod=0\nr.ReflectionMethod=0\n",
    )
    for script in ("ue_build_scene.py", "ue_inspect_scene.py"):
        workspace.write_bytes("Tools/" + script, (SCRIPTS / script).read_bytes())
    workspace.write_bytes("Tools/scene_contract.py", (Path(__file__).parent / "contract.py").read_bytes())
    for mode in ("build", "inspect"):
        job = {
            **marker,
            "mode": mode,
            "plan": str(workspace.root / "Requests/scene-plan.json"),
            "map_path": plan.map_path,
            "report": str(root / (mode + "-observation.json")),
            "capture_root": str(root / "captures"),
            "deadline_seconds": 480,
        }
        workspace.write_bytes("Requests/" + mode + "-job.json", json.dumps(job, indent=2).encode())
    manifest = {
        "contract": "SceneAssetContract",
        "schema_version": 1,
        "scene_id": plan.scene_id,
        "revision": plan.revision,
        "parent_manifest_sha256": plan.parent_manifest_sha256,
        "owner": "graphics",
        "protected_engineer_ids": list(plan.protected_engineer_ids),
        "engineer_integrated": False,
        "map_path": plan.map_path,
        "units": "cm",
        "axes": {"forward": "+X", "right": "+Y", "up": "+Z", "handedness": "left"},
        "scene_method": "native_unreal_editor_operations",
        "geometry_method": "native_unreal_basic_mesh_assembly",
        "actors": [{"id": a.id, "owner": a.owner} for a in plan.actors],
        "material_ids": [m.id for m in plan.materials],
        "plan_sha256": marker["plan_sha256"],
        "project": str(workspace.root / "ApexGraphics.uproject"),
        "nonce": marker["nonce"],
        "engine_created": False,
        "engine_judged": False,
        "status": "prepared_unverified",
    }
    (root / "scene-contract.json").write_text(json.dumps(manifest, indent=2))
    return manifest
