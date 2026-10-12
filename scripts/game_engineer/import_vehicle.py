"""Prepared UE editor import probe. Not run or qualified in the CPU cloud without UE."""

import argparse
import json
from pathlib import Path

import unreal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    project = Path(unreal.SystemLibrary.get_project_directory()).resolve()
    normal = str(project).replace("\\", "/").lower().replace("//?/", "").rstrip("/")
    if any(normal == p or normal.startswith(p + "/") for p in ("c:/gameproject", "/mnt/c/gameproject")):
        raise RuntimeError("protected_user_project")
    request_path = args.request.resolve()
    if project not in request_path.parents:
        raise RuntimeError("request_outside_isolated_project")
    marker = json.loads((project / ".apex-game-engineer-project.json").read_text())
    request = json.loads(request_path.read_text())
    if marker["nonce"] != request["nonce"] or marker["kind"] != "isolated_editor_import_only":
        raise RuntimeError("unverified_isolated_project")
    glb, report = Path(request["glb"]).resolve(), Path(request["report"]).resolve()
    if (
        project not in glb.parents
        or report.parent != project
        or request["destination"] != "/Game/APEXProbe"
    ):
        raise RuntimeError("isolated_import_paths_required")
    task = unreal.AssetImportTask()
    for name, value in {
        "filename": str(glb),
        "destination_path": request["destination"],
        "automated": True,
        "save": True,
        "replace_existing": False,
    }.items():
        task.set_editor_property(name, value)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = list(task.get_editor_property("imported_object_paths"))
    observations = []
    for name in paths:
        asset = unreal.load_asset(name)
        observations.append({"path": name, "class": asset.get_class().get_name() if asset else None})
    report.write_text(
        json.dumps(
            {
                "nonce": request["nonce"],
                "imported": observations,
                "engine_version": unreal.SystemLibrary.get_engine_version(),
                "project": str(project),
                "scale_axes_physics_verified": False,
                "driving_verified": False,
            },
            indent=2,
        )
    )
    if not paths:
        raise RuntimeError("engine_import_produced_no_assets")


if __name__ == "__main__":
    main()
