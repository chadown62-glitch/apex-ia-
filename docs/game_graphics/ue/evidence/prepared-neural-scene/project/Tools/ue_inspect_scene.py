"""Independent native UE re-open, actor/material/geometry checks and actual multiview screenshots."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import unreal

PROJECT = Path(unreal.SystemLibrary.get_project_directory()).resolve()
sys.path.insert(0, str(PROJECT / "Tools"))
from scene_contract import ScenePlan  # noqa: E402 - verified trusted private copy


def load_job():
    normal = str(PROJECT).replace("\\", "/").lower().replace("//?/", "").rstrip("/")
    if any(normal == p or normal.startswith(p + "/") for p in ("c:/gameproject", "/mnt/c/gameproject")):
        raise RuntimeError("protected_personal_project")
    request = Path(os.environ["APEX_GRAPHICS_UE_JOB"]).resolve()
    if request.parent != PROJECT / "Requests":
        raise RuntimeError("inspection_request_outside_private_project")
    marker = json.loads((PROJECT / ".apex-graphics-project.json").read_text())
    job = json.loads(request.read_text())
    plan_file = Path(job["plan"]).resolve()
    if (
        job["nonce"] != marker["nonce"]
        or job["mode"] != "inspect"
        or plan_file != PROJECT / "Requests/scene-plan.json"
    ):
        raise RuntimeError("unverified_inspection_job")
    if hashlib.sha256(plan_file.read_bytes()).hexdigest() != marker["plan_sha256"]:
        raise RuntimeError("unverified_inspection_plan")
    plan = ScenePlan.parse(json.loads(plan_file.read_text()))
    if (
        job["map_path"] != plan.map_path
        or plan.scene_id != marker["scene_id"]
        or plan.revision != marker["revision"]
    ):
        raise RuntimeError("inspection_scene_version_mismatch")
    if (
        Path(job["capture_root"]).resolve().parent != PROJECT.parent
        or Path(job["report"]).resolve().parent != PROJECT.parent
    ):
        raise RuntimeError("inspection_output_outside_workspace")
    return job, plan


def vec(value):
    return [value.x, value.y, value.z]


def inspect(job, plan):
    editor = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not editor.load_level(plan.map_path):
        raise RuntimeError("independent_level_reload_failed")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors, unexpected = {}, []
    for actor in subsystem.get_all_level_actors():
        tags = [str(t) for t in actor.tags]
        if "APEX_OWNER=engineer" in tags:
            raise RuntimeError("unqualified_engineer_dependency_present")
        ids = [t.removeprefix("APEX_ID=") for t in tags if t.startswith("APEX_ID=")]
        if not ids:
            continue
        if len(ids) != 1 or ids[0] in actors or "APEX_OWNER=graphics" not in tags:
            raise RuntimeError("duplicate_or_unowned_scene_identifier")
        identity = ids[0]
        rotation = actor.get_actor_rotation()
        row = {
            "id": identity,
            "class": actor.get_class().get_name(),
            "owner": "graphics",
            "location_cm": vec(actor.get_actor_location()),
            "rotation_deg": [rotation.pitch, rotation.yaw, rotation.roll],
            "tags": tags,
            "actor_path": actor.get_path_name(),
        }
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        if component:
            mesh = component.get_editor_property("static_mesh")
            minimum, maximum = component.get_local_bounds()
            scale = vec(actor.get_actor_scale3d())
            size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
            mat = component.get_material(0)
            row.update(
                {
                    "dimensions_cm": [s * factor for s, factor in zip(size, scale, strict=True)],
                    "mesh_path": mesh.get_path_name(),
                    "triangles": mesh.get_num_triangles(0),
                    "lod_count": mesh.get_num_lods(),
                    "vertices": unreal.get_editor_subsystem(
                        unreal.StaticMeshEditorSubsystem
                    ).get_number_verts(mesh, 0),
                    "material_id": unreal.EditorAssetLibrary.get_metadata_tag(mat, "APEX_MATERIAL_ID"),
                    "material_path": mat.get_path_name(),
                    "simulate_physics": component.is_simulating_physics(),
                    "collision_profile": str(component.get_collision_profile_name()),
                }
            )
            library = unreal.MaterialEditingLibrary
            row["roughness"] = library.get_material_default_scalar_parameter_value(mat, "APEX_Roughness")
            row["metallic"] = library.get_material_default_scalar_parameter_value(mat, "APEX_Metallic")
            color = library.get_material_default_vector_parameter_value(mat, "APEX_BaseColor")
            row["base_color"] = [color.r, color.g, color.b]
            row["pbr_connections"] = all(
                library.get_material_property_input_node(mat, prop) is not None
                for prop in (
                    unreal.MaterialProperty.MP_BASE_COLOR,
                    unreal.MaterialProperty.MP_ROUGHNESS,
                    unreal.MaterialProperty.MP_METALLIC,
                )
            )
        else:
            light = actor.get_component_by_class(unreal.LightComponent)
            if light:
                row["intensity"] = light.get_editor_property("intensity")
            else:
                unexpected.append(identity)
        actors[identity] = row
    return {
        "status": "inspected",
        "nonce": job["nonce"],
        "plan_sha256": job["plan_sha256"],
        "scene_id": plan.scene_id,
        "revision": plan.revision,
        "map_path": plan.map_path,
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "actors": actors,
        "unexpected": unexpected,
        "fps": None,
        "fps_reason": "Editor/Slate ticks are not a game benchmark",
        "captures": [],
        "reference_calibrated": False,
        "texture_maps_qualified": False,
    }


def begin_capture(job, observation):
    output = Path(job["capture_root"])
    output.mkdir(exist_ok=True)
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    # Inspector's camera is temporary and never saved into the artist's level.
    camera = subsystem.spawn_actor_from_class(unreal.CameraActor, unreal.Vector(0, 0, 0), transient=True)
    camera.set_actor_label("APEX_IndependentInspectionCamera")
    positions = [(-2500, -2200, 1500), (2200, 2200, 1400), (0, -2500, 1500), (-2400, 1300, 1700)]
    state = {"index": 0, "task": None, "started": time.monotonic(), "callback": None}

    def finish(error=None):
        if error:
            observation["status"], observation["error"] = "error", error
        observation["capture_elapsed_s"] = time.monotonic() - state["started"]
        Path(job["report"]).write_text(json.dumps(observation, indent=2))
        unreal.unregister_slate_post_tick_callback(state["callback"])
        unreal.SystemLibrary.quit_editor()

    def tick(delta_seconds):
        try:
            if time.monotonic() - state["started"] > job["deadline_seconds"]:
                finish("capture_deadline_exceeded")
                return
            if state["task"] is not None:
                if not state["task"].is_task_done():
                    return
                path = output / f"view_{state['index']}.png"
                if not path.is_file():
                    finish("native_screenshot_missing")
                    return
                observation["captures"].append(
                    {
                        "view": state["index"],
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "source": "Unreal.AutomationLibrary",
                        "camera_location_cm": vec(camera.get_actor_location()),
                    }
                )
                state["index"] += 1
                state["task"] = None
            if state["index"] == len(positions):
                finish()
                return
            location = unreal.Vector(*positions[state["index"]])
            rotation = unreal.MathLibrary.find_look_at_rotation(location, unreal.Vector(0, 350, 180))
            camera.set_actor_location_and_rotation(location, rotation, False, True)
            state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(
                640, 420, str(output / f"view_{state['index']}.png"), camera=camera, delay=2.0
            )
        except Exception as exc:
            finish(type(exc).__name__ + ": " + str(exc))

    state["callback"] = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    try:
        job, plan = load_job()
        begin_capture(job, inspect(job, plan))
    except Exception as exc:
        if "job" in globals():
            Path(job["report"]).write_text(
                json.dumps(
                    {
                        "status": "error",
                        "nonce": job["nonce"],
                        "error": type(exc).__name__ + ": " + str(exc),
                    },
                    indent=2,
                )
            )
        unreal.SystemLibrary.quit_editor()
        raise
